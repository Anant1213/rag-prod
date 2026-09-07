from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # data plane
    database_url: str = "postgresql://rag:rag@localhost:5432/rag"

    # model plane (any OpenAI-compatible endpoint: Ollama, LiteLLM, vLLM, OpenAI)
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "not-needed"
    llm_model: str = "llama3.1:latest"
    llm_timeout_s: float = 60.0

    # retrieval
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dim: int = 384
    candidates_k: int = 20          # per retriever, before fusion
    final_k: int = 5                # chunks sent to the LLM

    # Cross-encoder rerank of the fused shortlist. Costs ~1 forward pass per
    # candidate, so it is bounded by rerank_candidates, not by corpus size.
    rerank_enabled: bool = True
    # jina-turbo over ms-marco-MiniLM: on the golden set the MiniLM scored the
    # correct chunk at 0.001 for "reindex vectors without downtime" (rank 4);
    # jina puts it at rank 1. ~150MB vs ~80MB, same ONNX runtime.
    rerank_model: str = "jinaai/jina-reranker-v1-turbo-en"
    rerank_candidates: int = 20

    # Concurrent ONNX passes per pod, across embedding and reranking. Guards the
    # memory limit: model inference is the only unbounded allocation in the
    # request path, so this is what stands between a traffic burst and an
    # OOMKill. Keep it near the pod's CPU allocation -- more threads than cores
    # buys queueing inside the process instead of throughput.
    model_concurrency: int = 2
    # Below this we refuse instead of hallucinating. Now that reranking replaces
    # the RRF score with a 0-1 relevance probability, this is a real threshold
    # rather than a guess against RRF's 1/(k+rank) ceiling.
    #
    # Measured over the golden set and a set of off-topic questions:
    #   on-topic  top-1 spans 0.321 .. 0.896  (lowest: "reindex vectors")
    #   off-topic top-1 spans 0.015 .. 0.317  (highest: "how do I bake sourdough")
    # The two nearly touch, so no threshold separates them cleanly. 0.20 keeps a
    # 0.12 margin under the weakest real question and still refuses four of five
    # off-topic ones; the prompt's grounding rule catches whatever gets through,
    # because a wrong refusal is worse for the user than a wasted LLM call.
    min_score: float = 0.20

    # ops
    service_name: str = "rag-chatbot"
    service_version: str = "0.1.0"
    otel_endpoint: str | None = None  # e.g. http://otel-collector:4317
    log_level: str = "INFO"


settings = Settings()
