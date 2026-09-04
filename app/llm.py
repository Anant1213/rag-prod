import json
from collections.abc import AsyncIterator

import httpx

from app.config import settings
from app.schemas import Chunk

SYSTEM = (
    "You answer strictly from the numbered context below. "
    "Cite the sources you use as [1], [2] and so on. "
    "If the context does not contain the answer, say you do not know. "
    "Never invent commands, versions or file paths."
)


def build_prompt(question: str, chunks: list[Chunk]) -> list[dict]:
    context = "\n\n".join(
        f"[{i}] source={c.source}\n{c.text}" for i, c in enumerate(chunks, start=1)
    )
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
    ]


async def stream_completion(messages: list[dict]) -> AsyncIterator[str]:
    """Streams deltas from any OpenAI-compatible /chat/completions endpoint."""
    payload = {"model": settings.llm_model, "messages": messages, "stream": True}
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"

    async with (
        httpx.AsyncClient(timeout=settings.llm_timeout_s) as client,
        client.stream("POST", url, json=payload, headers=headers) as r,
    ):
        r.raise_for_status()
        async for line in r.aiter_lines():
            if not line.startswith("data: "):
                continue
            blob = line[6:].strip()
            if blob == "[DONE]":
                break
            try:
                delta = json.loads(blob)["choices"][0]["delta"]
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
            if content := delta.get("content"):
                yield content
