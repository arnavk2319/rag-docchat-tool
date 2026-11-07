"""POST /v1/summarize: map-reduce summary (services/summarization.py)."""

from fastapi import APIRouter, Depends

from ..core.concurrency import run_blocking
from ..services.summarization import DocText, summarize_documents
from .deps import CurrentUser, ParentsDep, RegistryDep, json_body_limit
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
    body: SummarizeRequest, user: CurrentUser, registry: RegistryDep, parents: ParentsDep
) -> SummarizeResponse:
    ids = body.ids()
    docs = await resolve_ready_docs(registry, user, ids)
    texts = []
    for d in docs:
        plist = await run_blocking(parents.list_doc_parents, user, d.doc_id)
        texts.append(DocText(d.filename, [p.text for p in plist]))
    res = await summarize_documents(texts)  # LLMError -> 502 via app exception handlers
    return SummarizeResponse(summary=res.summary, doc_ids=ids, partial=res.partial)
