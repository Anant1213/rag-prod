import logging
import sys

from prometheus_client import Counter, Gauge, Histogram

from app.config import settings

# ---- Prometheus: the four numbers you will actually alert on ----
REQUESTS = Counter(
    "rag_requests_total", "Chat requests", ["route", "outcome"]
)
LATENCY = Histogram(
    "rag_request_duration_seconds",
    "End-to-end request latency",
    ["route"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32),
)
TTFT = Histogram(
    "rag_time_to_first_token_seconds",
    "Time to first streamed token",
    buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8),
)
RETRIEVAL_LATENCY = Histogram(
    "rag_retrieval_duration_seconds", "Retrieval latency", ["retriever"]
)
TOKENS = Counter("rag_tokens_total", "Tokens processed", ["direction"])
REFUSALS = Counter(
    "rag_refusals_total", "Answers refused for lack of grounding"
)
EMPTY_RETRIEVAL = Counter(
    "rag_empty_retrieval_total", "Queries that matched zero chunks"
)
READY = Gauge("rag_ready", "1 when dependencies are healthy")


def setup_logging() -> None:
    logging.basicConfig(
        level=settings.log_level,
        stream=sys.stdout,
        format='{"ts":"%(asctime)s","level":"%(levelname)s",'
        '"logger":"%(name)s","msg":"%(message)s"}',
    )


def setup_tracing(app) -> None:
    """OTel traces. No-ops cleanly when OTEL_ENDPOINT is unset."""
    if not settings.otel_endpoint:
        return
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create(
        {
            "service.name": settings.service_name,
            "service.version": settings.service_version,
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint, insecure=True))
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")


def tracer():
    from opentelemetry import trace

    return trace.get_tracer(settings.service_name)
