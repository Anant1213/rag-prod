from pydantic import BaseModel, Field


class Chunk(BaseModel):
    chunk_id: int
    doc_id: str
    source: str
    text: str
    score: float


class ChatRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    tenant: str = "default"
    final_k: int | None = None


class SearchResponse(BaseModel):
    query: str
    chunks: list[Chunk]
