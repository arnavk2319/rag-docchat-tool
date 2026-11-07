"""POST /v1/summarize: map-reduce summary (services/summarization.py)."""

from fastapi import APIRouter, Depends

from ..core.concurrency import run_blocking
from ..core.llm import Budget
from ..core.security import sanitize_output
from ..services.summarization import DocText, summarize_documents
from .deps import CurrentUser, ParentsDep, RegistryDep, SummarizeLease, json_body_limit
from .rag import resolve_ready_docs
from .schemas import ErrorBody, SummarizeRequest, SummarizeResponse

router = APIRouter(prefix="/v1", tags=["query"])

@router.post(
    "/summarize",
    response_model=SummarizeResponse,
    dependencies=[Depends(json_body_limit)],
    responses={
        401: {"model": ErrorBody},
        404: {"model": ErrorBody},
        409: {"model": ErrorBody},
        429: {"model": ErrorBody},
        502: {"model": ErrorBody},
    },
)
async def summarize(
    body: SummarizeRequest, user: CurrentUser, registry: RegistryDep, parents: ParentsDep,
    lease: SummarizeLease,
) -> SummarizeResponse:
    used: list[Budget] = []
    try:
        ids = body.ids()
        docs = await resolve_ready_docs(registry, user, ids)
        await run_blocking(lease.acquire)  # daily caps, before any LLM call
        texts = []
        for d in docs:
            plist = await run_blocking(parents.list_doc_parents, user, d.doc_id)
            texts.append(DocText(d.filename, [p.text for p in plist]))
        res = await summarize_documents(texts, budget_out=used)  # LLMError -> 502 via handlers
    finally:  # actual spend, also on error/cancel
        lease.finish(used[0].usd if used else 0.0)
    return SummarizeResponse(summary=sanitize_output(res.summary), doc_ids=ids, partial=res.partial)
