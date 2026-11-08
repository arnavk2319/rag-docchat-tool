"""POST /v1/compare (services/compare.py)."""

from fastapi import APIRouter

from ..core.concurrency import run_blocking
from ..core.llm import Budget
from ..core.security import sanitize_output
from ..services.compare import Point, compare_docs
from .deps import CurrentUser, ParentsDep, RegistryDep, SummarizeLease
from .docintel import _cite
from .rag import resolve_ready_docs
from .schemas import Citation, ComparePoint, CompareRequest, CompareResponse, ErrorBody

router = APIRouter(prefix="/v1", tags=["documents"])
_ERRORS = {c: {"model": ErrorBody} for c in (401, 404, 409, 429, 502)}


@router.post("/compare", response_model=CompareResponse, responses=_ERRORS)
async def compare(
    body: CompareRequest, user: CurrentUser, registry: RegistryDep, parents: ParentsDep,
    lease: SummarizeLease,
) -> CompareResponse:
    ids = list(dict.fromkeys(body.doc_ids))
    used: list[Budget] = []
    try:
        docs = await resolve_ready_docs(registry, user, ids)  # any foreign/unknown -> 404, no LLM
        await run_blocking(lease.acquire)  # daily caps, before any LLM call
        per_doc = [(d.doc_id, await run_blocking(parents.list_doc_parents, user, d.doc_id)) for d in docs]
        res = await compare_docs(per_doc, body.focus, used)  # LLMError -> 502 via handlers
    finally:
        lease.finish(used[0].usd if used else 0.0)
    names = {d.doc_id: d.filename for d in docs}

    def out(pts: list[Point]) -> list[ComparePoint]:
        return [ComparePoint(point=sanitize_output(x.point), citations=[
            Citation.model_validate(_cite(p, names[p.doc_id])) for p in x.cited]) for x in pts]

    return CompareResponse(doc_ids=ids, similarities=out(res.similarities), differences=out(res.differences),
                           abstained=res.abstained, partial=res.partial)
