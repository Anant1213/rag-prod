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
    min_score: float = 0.02         # below this we refuse instead of hallucinating

    # ops
    service_name: str = "rag-chatbot"
    service_version: str = "0.1.0"
    otel_endpoint: str | None = None  # e.g. http://otel-collector:4317
    log_level: str = "INFO"


settings = Settings()
