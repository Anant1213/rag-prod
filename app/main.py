import json
import time
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app import db, embeddings
from app.config import settings
from app.llm import build_prompt, stream_completion
from app.retrieval import retrieve
from app.schemas import ChatRequest, SearchResponse
from app.telemetry import (
    LATENCY,
    READY,
    REFUSALS,
    REQUESTS,
    TOKENS,
    TTFT,
    setup_logging,
    setup_tracing,
    tracer,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await db.open_pool()
    await anyio.to_thread.run_sync(embeddings.warm)  # pay the model load before readiness
    READY.set(1)
    yield
    READY.set(0)
    await db.close_pool()


app = FastAPI(title="rag-chatbot", version=settings.service_version, lifespan=lifespan)
setup_tracing(app)


# ---- probes: liveness must NOT touch dependencies, readiness must ----
@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"status": "alive"}


@app.get("/readyz", include_in_schema=False)
async def readyz():
    if not await db.ping():
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"status": "ready", "version": settings.service_version}


@app.get("/metrics", include_in_schema=False)
async def metrics():
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/search", response_model=SearchResponse)
async def search(req: ChatRequest):
    started = time.perf_counter()
    chunks = await retrieve(req.question, req.tenant, req.final_k)
    LATENCY.labels("search").observe(time.perf_counter() - started)
    REQUESTS.labels("search", "ok").inc()
    return SearchResponse(query=req.question, chunks=chunks)


@app.post("/chat")
async def chat(req: ChatRequest):
    """SSE stream. First event carries the citations so the UI can render them immediately."""
    started = time.perf_counter()
    chunks = await retrieve(req.question, req.tenant, req.final_k)

    # Guardrail: no grounding, no answer. This is the cheapest hallucination control there is.
    if not chunks or chunks[0].score < settings.min_score:
        REFUSALS.inc()
        REQUESTS.labels("chat", "refused").inc()
        LATENCY.labels("chat").observe(time.perf_counter() - started)

        async def refusal():
            payload = {"answer": "I don't have anything in the knowledge base that answers that."}
            yield f"event: refusal\ndata: {json.dumps(payload)}\n\n"

        return StreamingResponse(refusal(), media_type="text/event-stream")

    async def event_stream():
        first = True
        sources = [
            {"n": i, "source": c.source, "doc_id": c.doc_id, "score": round(c.score, 4)}
            for i, c in enumerate(chunks, start=1)
        ]
        yield f"event: sources\ndata: {json.dumps(sources)}\n\n"
        try:
            with tracer().start_as_current_span("generate") as span:
                span.set_attribute("rag.model", settings.llm_model)
                async for token in stream_completion(build_prompt(req.question, chunks)):
                    if first:
                        TTFT.observe(time.perf_counter() - started)
                        first = False
                    TOKENS.labels("output").inc()
                    yield f"data: {json.dumps({'delta': token})}\n\n"
            REQUESTS.labels("chat", "ok").inc()
        except Exception as exc:  # noqa: BLE001 - any upstream model failure becomes an SSE error event
            REQUESTS.labels("chat", "error").inc()
            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
        finally:
            LATENCY.labels("chat").observe(time.perf_counter() - started)
            yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
