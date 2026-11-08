"""POST /v1/documents/{doc_id}/summary and /sentiment (services/docintel.py)."""

from collections.abc import Awaitable, Callable, Sequence

from fastapi import APIRouter

from ..core.concurrency import run_blocking
from ..core.llm import Budget
from ..core.security import sanitize_output
from ..services.docintel import DocIntelResult, classify_doc, summarize_doc
from ..store.parents import Parent
from .deps import CurrentUser, ParentsDep, RegistryDep, SummarizeLease
from .rag import resolve_ready_docs
from .schemas import Citation, DocSentimentResponse, DocSummaryResponse, ErrorBody, Span

router = APIRouter(prefix="/v1", tags=["documents"])
_ERRORS = {c: {"model": ErrorBody} for c in (401, 404, 409, 429, 502)}


def _cite(p: Parent, filename: str) -> Citation:
    return Citation(doc_id=p.doc_id, filename=filename, chunk_id=p.parent_id, page=p.page,
                    sheet=p.sheet, span=Span(char_start=p.char_start, char_end=p.char_end))


async def _run(
    doc_id: str, user: str, registry, parents, lease,
    fn: Callable[[Sequence[Parent], list[Budget]], Awaitable[DocIntelResult]],
) -> tuple[DocIntelResult, list[Citation]]:
    used: list[Budget] = []
    try:
        (doc,) = await resolve_ready_docs(registry, user, [doc_id])  # foreign/unknown -> 404
        await run_blocking(lease.acquire)  # daily caps, before any LLM call
        plist = await run_blocking(parents.list_doc_parents, user, doc.doc_id)
        res = await fn(plist, used)  # LLMError -> 502 via handlers
    finally:
        lease.finish(used[0].usd if used else 0.0)
    return res, [Citation.model_validate(_cite(p, doc.filename)) for p in res.cited]


@router.post("/documents/{doc_id}/summary", response_model=DocSummaryResponse, responses=_ERRORS)
async def document_summary(
    doc_id: str, user: CurrentUser, registry: RegistryDep, parents: ParentsDep, lease: SummarizeLease,
) -> DocSummaryResponse:
    res, cites = await _run(doc_id, user, registry, parents, lease, lambda p, b: summarize_doc(p, b))
    return DocSummaryResponse(doc_id=doc_id, summary=sanitize_output(res.summary), citations=cites,
                              abstained=res.abstained, partial=res.partial)


@router.post("/documents/{doc_id}/sentiment", response_model=DocSentimentResponse, responses=_ERRORS)
async def document_sentiment(
    doc_id: str, user: CurrentUser, registry: RegistryDep, parents: ParentsDep, lease: SummarizeLease,
) -> DocSentimentResponse:
    res, cites = await _run(doc_id, user, registry, parents, lease, lambda p, b: classify_doc(p, b))
    return DocSentimentResponse(
        doc_id=doc_id, label=res.label, confidence=res.confidence,
        rationale=sanitize_output(res.rationale), citations=cites,
        abstained=res.abstained, partial=res.partial)
