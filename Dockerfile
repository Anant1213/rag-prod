FROM python:3.12-slim AS builder
WORKDIR /build
RUN pip install --no-cache-dir uv
COPY pyproject.toml .
RUN uv pip install --system --no-cache -r pyproject.toml

FROM python:3.12-slim
# non-root, no shell tools we don't need
RUN useradd -u 10001 -m app
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY app ./app
COPY scripts ./scripts
COPY eval ./eval
# bake the embedding model into the image so pods don't download weights at startup
ENV HF_HOME=/app/.cache
RUN mkdir -p /app/.cache && chown -R app /app
USER app
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "30"]
