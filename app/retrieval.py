"""Hybrid retrieval: dense (pgvector/HNSW) + lexical (Postgres FTS), fused with RRF."""
import time

from app.config import settings
from app.db import pool
from app.embeddings import embed_query
from app.rerank import rerank
from app.schemas import Chunk
from app.telemetry import EMPTY_RETRIEVAL, RETRIEVAL_LATENCY, tracer

DENSE_SQL = """
select chunk_id, doc_id, source, text, page, 1 - (embedding <=> %s::vector) as score
from chunks
where tenant = %s
order by embedding <=> %s::vector
limit %s
"""

# plainto_tsquery ANDs every term, so a natural-language question ("how do I
# reindex vectors without downtime?") demands one chunk contain all of them and
# usually matches nothing -- silently reducing hybrid search to dense-only, which
# only shows up once the corpus is big enough for dense to miss.
#
# AND is still the right first choice: when some chunk really does contain every
# term it is almost always the answer. So try AND, and fall back to OR only when
# AND matches nothing, rather than paying OR's noise on every query.
LEXICAL_SQL = """
with strict as (select plainto_tsquery('english', %s) as tsq),
     loose  as (select replace(plainto_tsquery('english', %s)::text, '&', '|')::tsquery as tsq),
     q as (
        select case
                 when exists (
                     select 1 from chunks c, strict
                     where c.tenant = %s and c.tsv @@ strict.tsq
                 )
                 then (select tsq from strict)
                 else (select tsq from loose)
               end as tsq
     )
select chunk_id, doc_id, source, text, page,
       ts_rank_cd(chunks.tsv, q.tsq) as score
from chunks, q
where chunks.tenant = %s and chunks.tsv @@ q.tsq
order by score desc
limit %s
"""


async def _run(sql: str, params: tuple) -> list[dict]:
    async with pool().connection() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
        cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def rrf(rankings: list[list[dict]], k: int = 60) -> list[dict]:
    """Reciprocal Rank Fusion. Rank-based, so incomparable score scales don't matter."""
    fused: dict[int, dict] = {}
    for ranking in rankings:
        for rank, row in enumerate(ranking, start=1):
            entry = fused.setdefault(row["chunk_id"], {**row, "score": 0.0})
            entry["score"] += 1.0 / (k + rank)
    return sorted(fused.values(), key=lambda r: r["score"], reverse=True)


async def retrieve(question: str, tenant: str = "default", final_k: int | None = None) -> list[Chunk]:
    final_k = final_k or settings.final_k
    with tracer().start_as_current_span("retrieve") as span:
        span.set_attribute("rag.tenant", tenant)
        span.set_attribute("rag.question_chars", len(question))

        t0 = time.perf_counter()
        vector = await embed_query(question)
        RETRIEVAL_LATENCY.labels("embed").observe(time.perf_counter() - t0)

        t0 = time.perf_counter()
        dense = await _run(
            DENSE_SQL, (vector, tenant, vector, settings.candidates_k)
        )
        RETRIEVAL_LATENCY.labels("dense").observe(time.perf_counter() - t0)

        t0 = time.perf_counter()
        lexical = await _run(
            LEXICAL_SQL, (question, question, tenant, tenant, settings.candidates_k)
        )
        RETRIEVAL_LATENCY.labels("lexical").observe(time.perf_counter() - t0)

        merged = rrf([dense, lexical])
        if settings.rerank_enabled and merged:
            shortlist = merged[: settings.rerank_candidates]
            t0 = time.perf_counter()
            scores = await rerank(question, [c["text"] for c in shortlist])
            RETRIEVAL_LATENCY.labels("rerank").observe(time.perf_counter() - t0)
            for row, score in zip(shortlist, scores):
                row["score"] = score          # replaces the RRF score, now 0-1
            merged = sorted(shortlist, key=lambda r: r["score"], reverse=True)

        merged = merged[:final_k]
        if not merged:
            EMPTY_RETRIEVAL.inc()

        span.set_attribute("rag.dense_hits", len(dense))
        span.set_attribute("rag.lexical_hits", len(lexical))
        span.set_attribute("rag.reranked", settings.rerank_enabled)
        span.set_attribute("rag.chunk_ids", str([m["chunk_id"] for m in merged]))
        return [Chunk(**m) for m in merged]
