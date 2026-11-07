"""POST /v1/query: single-shot hybrid retrieval + one `agent` LLM call, with citations.

`session_id` and `stream` are accepted in the schema but ignored here: sessions arrive in
T-020 and SSE streaming in T-019 (ADR-0002 sections 4-5). `abstained` is true only when
retrieval returns nothing or the top rerank score is below MIN_RERANK_SCORE (no LLM call).
Pipeline: hybrid top-N -> parents -> cross-encoder rerank -> top_k parents -> LLM.
"""

from fastapi import APIRouter, Depends

from ..core import llm
from ..core.concurrency import run_blocking
from ..core.config import get_settings
from ..core.models import get_models
from ..core.prompts import load_prompt
from ..services.parent_lookup import expand_to_parents
from ..services.rerank import pick_cited_children, rerank_parents
from ..services.retrieval import TOP_N, hybrid_search
from .deps import (
    Bm25Dep,
    CurrentUser,
    ParentsDep,
    RegistryDep,
    StoreDep,
    json_body_limit,
)
from .rag import citation_for, embed_one, escape, resolve_ready_docs
from .schemas import ErrorBody, QueryRequest, QueryResponse

router = APIRouter(prefix="/v1", tags=["query"])
IDK = "I don't know."


@router.post(
    "/query",
    response_model=QueryResponse,
    dependencies=[Depends(json_body_limit)],
    responses={
        401: {"model": ErrorBody},
        404: {"model": ErrorBody},
        409: {"model": ErrorBody},
        429: {"model": ErrorBody},
        502: {"model": ErrorBody},
    },
)
async def query(
    body: QueryRequest,
    user: CurrentUser,
    registry: RegistryDep,
    store: StoreDep,
    parents: ParentsDep,
    bm25: Bm25Dep,
) -> QueryResponse:
    if body.doc_ids is not None:
        docs = await resolve_ready_docs(registry, user, list(dict.fromkeys(body.doc_ids)))
    else:
        docs = [d for d in await run_blocking(registry.list_by_owner, user) if d.status == "ready"]
    names = {d.doc_id: d.filename for d in docs}
    if not names:
        return QueryResponse(answer=IDK, citations=[], abstained=True)

    vec = await embed_one(body.question)
    # Hybrid (BM25 + dense, RRF), always scoped to the resolved ready docs of this owner.
    hits = await run_blocking(
        hybrid_search,
        user,
        body.question,
        vec,
        store,
        bm25,
        list(names),
        max(body.top_k, TOP_N),
    )
    if not hits:
        return QueryResponse(answer=IDK, citations=[], abstained=True)

    # Candidates -> deduplicated parents -> cross-encoder rerank to top_k parents.
    cands = await run_blocking(expand_to_parents, user, hits, parents)
    ranked = await run_blocking(
        rerank_parents, get_models().reranker, body.question, cands, body.top_k
    )
    if not ranked or ranked[0][1] < get_settings().min_rerank_score:
        return QueryResponse(answer=IDK, citations=[], abstained=True)
    phits = [ph for ph, _ in ranked]
    context = "\n".join(
        f'<excerpt id="c{i}" source="{escape(names[ph.parent.doc_id])}">\n'
        f"{escape(ph.parent.text)}\n</excerpt>"
        for i, ph in enumerate(phits, 1)
    )
    prompt = load_prompt("answer")
    res = await llm.complete(
        [
            {
                "role": "user",
                "content": prompt.render(context=context, question=escape(body.question)),
            }
        ],
        model_role="agent",
        max_tokens=1024,
    )
    best = await run_blocking(
        pick_cited_children, get_models().reranker, body.question, phits, hits
    )
    cites = [citation_for(best[ph.parent.parent_id], names[ph.parent.doc_id]) for ph in phits]
    return QueryResponse(answer=res.text.strip(), citations=cites, abstained=False)
