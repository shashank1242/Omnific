from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

DocStatus = Literal["pending", "ingesting", "ready", "failed"]
ChunkKind = Literal["text", "table", "figure"]


class Document(BaseModel):
    id: str
    filename: str
    content_type: str
    title: str | None = None
    pages: int | None = None
    status: DocStatus = "pending"
    error: str | None = None
    n_chunks: int = 0
    n_figures: int = 0
    n_tables: int = 0
    created_at: datetime | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class ChunkIn(BaseModel):
    ord: int
    kind: ChunkKind = "text"
    page: int | None = None
    section: str | None = None
    context: str | None = None
    content: str
    embedding: list[float]


class Hit(BaseModel):
    document_id: str
    title: str | None = None
    page: int | None = None
    kind: ChunkKind = "text"
    section: str | None = None
    content: str
    score: float = 0.0
    citation_url: str
    ord: int | None = None


def citation_url(document_id: str, page: int | None) -> str:
    base = f"omnific://doc/{document_id}"
    return f"{base}#page={page}" if page is not None else base
