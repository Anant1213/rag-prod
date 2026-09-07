"""Cross-encoder reranking of the hybrid shortlist.

Retrieval embeds chunks once, offline, without ever seeing the question -- fast
enough to search the whole corpus, but the comparison is coarse. A cross-encoder
reads question and chunk *together* and is far more accurate, at a cost that only
makes sense over a shortlist. So hybrid search picks the candidates and this
picks the order.
"""
import math
from functools import lru_cache

import anyio.to_thread

from app.config import settings


@lru_cache(maxsize=1)
def _model():
    # ONNX, CPU-only, ~80MB. Same runtime as the embedding model, no torch.
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return TextCrossEncoder(model_name=settings.rerank_model)


def warm() -> None:
    list(_model().rerank("warmup", ["warmup"]))


def rerank_sync(question: str, texts: list[str]) -> list[float]:
    # Raw outputs are logits on an arbitrary scale. Squashing to 0-1 makes the
    # score a calibrated-ish relevance probability, which is what makes
    # settings.min_score a meaningful refusal threshold rather than a guess
    # against RRF's 1/(k+rank) ceiling.
    return [1.0 / (1.0 + math.exp(-s)) for s in _model().rerank(question, texts)]


@lru_cache(maxsize=1)
def _limiter() -> anyio.CapacityLimiter:
    """Bound how many model passes run at once.

    anyio's default thread pool is 40, so without a limiter a burst of traffic
    becomes 40 simultaneous ONNX sessions, each allocating for its own batch. At
    10 req/s this OOMKilled every pod inside 20 seconds -- 0 of 208 requests
    succeeded, because the pods died rather than queued. Capping concurrency
    turns overload into latency, which sheds load through timeouts instead of
    taking the process down.
    """
    return anyio.CapacityLimiter(settings.model_concurrency)


async def rerank(question: str, texts: list[str]) -> list[float]:
    return await anyio.to_thread.run_sync(
        lambda: rerank_sync(question, texts), limiter=_limiter()
    )
