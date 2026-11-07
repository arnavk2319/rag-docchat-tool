"""Request/response models for the HTTP API (one per entity; see ADR-0002)."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

Status = Literal["pending", "processing", "ready", "failed"]


class ErrorBody(BaseModel):
    detail: str
    code: str


class DocumentOut(BaseModel):
    doc_id: str
    filename: str
    mime: str
    size: int
    sha256: str
    pages_or_sheets: int
    status: Status
    failure_reason: str | None = None
    created_at: datetime


class DocumentList(BaseModel):
    items: list[DocumentOut]
    total: int


class UploadError(BaseModel):
    code: str
    detail: str


class UploadResult(BaseModel):
    filename: str
    ok: bool
    doc_id: str | None = None
    status: Status | None = None
    deduplicated: bool = False
    failure_reason: str | None = None
    error: UploadError | None = None


class UploadResponse(BaseModel):
    results: list[UploadResult]


class Span(BaseModel):
    char_start: int
    char_end: int


class Citation(BaseModel):
    doc_id: str
    filename: str
    chunk_id: str
    page: int | None = None
    sheet: str | None = None
    span: Span


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    doc_ids: list[str] | None = Field(default=None, min_length=1, max_length=20)
    top_k: int = Field(default=5, ge=1, le=20)
    session_id: str | None = Field(default=None, max_length=64)
    stream: bool = False

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("question must not be blank")
        return v


class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation]
    abstained: bool = False
    session_id: str | None = None


class SummarizeRequest(BaseModel):
    doc_id: str | None = None
    doc_ids: list[str] | None = Field(default=None, min_length=1, max_length=10)

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.doc_id is None) == (self.doc_ids is None):
            raise ValueError("provide exactly one of doc_id or doc_ids")
        return self

    def ids(self) -> list[str]:
        return list(dict.fromkeys([self.doc_id] if self.doc_id is not None else self.doc_ids or []))


class SummarizeResponse(BaseModel):
    summary: str
    doc_ids: list[str]
    partial: bool = False  # true when the USD cap or batch cap cut the summary short


class SessionCreated(BaseModel):
    session_id: str


class SessionMessageOut(BaseModel):
    role: str
    content: str
    citation_ids: list[str]
    created_at: datetime


class SessionOut(BaseModel):
    session_id: str
    messages: list[SessionMessageOut]


class DocSummaryResponse(BaseModel):
    doc_id: str
    summary: str
    citations: list[Citation]
    abstained: bool = False
    partial: bool = False  # document text was cut at the prompt cap


class DocSentimentResponse(BaseModel):
    doc_id: str
    label: str  # positive | negative | neutral | mixed | unclear
    confidence: float = Field(ge=0, le=1)
    rationale: str
    citations: list[Citation]
    abstained: bool = False
    partial: bool = False
