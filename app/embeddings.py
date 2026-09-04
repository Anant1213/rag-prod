from functools import lru_cache

import anyio.to_thread  # submodule import: anyio>=4.15 lazy-loads, bare `import anyio` is not enough

from app.config import settings


@lru_cache(maxsize=1)
def _model():
    # fastembed = ONNX, CPU-only, ~90MB. No torch in the image.
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=settings.embed_model)


def warm() -> None:
    _model().embed(["warmup"])


def embed_sync(texts: list[str], prefix: str = "") -> list[list[float]]:
    inputs = [prefix + t for t in texts]
    return [v.tolist() for v in _model().embed(inputs)]


async def embed_query(text: str) -> list[float]:
    # bge models want this prefix on queries only
    vectors = await anyio.to_thread.run_sync(
        lambda: embed_sync([text], prefix="Represent this sentence for searching relevant passages: ")
    )
    return vectors[0]
