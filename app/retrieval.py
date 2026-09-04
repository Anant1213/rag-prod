"""Hybrid retrieval: dense (pgvector/HNSW) + lexical (Postgres FTS), fused with RRF."""
import time

from app.config import settings
from app.db import pool
from app.embeddings import embed_query
from app.schemas import Chunk
from app.telemetry import EMPTY_RETRIEVAL, RETRIEVAL_LATENCY, tracer

DENSE_SQL = """
select chunk_id, doc_id, source, text, 1 - (embedding <=> %s::vector) as score
from chunks
where tenant = %s
order by embedding <=> %s::vector
limit %s
"""

LEXICAL_SQL = """
select chunk_id, doc_id, source, text,
       ts_rank_cd(tsv, websearch_to_tsquery('english', %s)) as score
from chunks
where tenant = %s and tsv @@ websearch_to_tsquery('english', %s)
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
            LEXICAL_SQL, (question, tenant, question, settings.candidates_k)
        )
        RETRIEVAL_LATENCY.labels("lexical").observe(time.perf_counter() - t0)

        merged = rrf([dense, lexical])[:final_k]
        if not merged:
            EMPTY_RETRIEVAL.inc()

        span.set_attribute("rag.dense_hits", len(dense))
        span.set_attribute("rag.lexical_hits", len(lexical))
        span.set_attribute("rag.chunk_ids", str([m["chunk_id"] for m in merged]))
        return [Chunk(**m) for m in merged]
