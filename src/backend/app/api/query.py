"""POST /v1/query: single-shot dense retrieval + one `agent` LLM call, with citations.

`session_id` and `stream` are accepted in the schema but ignored here: sessions arrive in
T-020 and SSE streaming in T-019 (ADR-0002 sections 4-5). `abstained` is true only when
retrieval returns nothing (no LLM call); score-based abstention arrives in T-010.
"""

from fastapi import APIRouter, Depends

from ..core import llm
from ..core.concurrency import run_blocking
from ..core.prompts import load_prompt
from ..services.parent_lookup import expand_to_parents
from .deps import CurrentUser, ParentsDep, RegistryDep, StoreDep, json_body_limit
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
    body: QueryRequest, user: CurrentUser, registry: RegistryDep, store: StoreDep, parents: ParentsDep
) -> QueryResponse:
    if body.doc_ids is not None:
        docs = await resolve_ready_docs(registry, user, list(dict.fromkeys(body.doc_ids)))
    else:
        docs = [d for d in await run_blocking(registry.list_by_owner, user) if d.status == "ready"]
    names = {d.doc_id: d.filename for d in docs}
    if not names:
        return QueryResponse(answer=IDK, citations=[], abstained=True)

    vec = await embed_one(body.question)
    if body.doc_ids is not None:
        # per-doc loop until the list filter lands in T-010
        hits = []
        for did in names:
            hits += await run_blocking(store.query, user, vec, body.top_k, did)
    else:
        hits = await run_blocking(store.query, user, vec, body.top_k * 2)
    hits = [h for h in hits if h.metadata.get("doc_id") in names]
    hits = sorted(hits, key=lambda h: h.distance)[: body.top_k]
    if not hits:
        return QueryResponse(answer=IDK, citations=[], abstained=True)

    # Prompt context = deduplicated PARENT texts in best-child order; citations stay on children.
    phits = await run_blocking(expand_to_parents, user, hits, parents)
    if not phits:
        return QueryResponse(answer=IDK, citations=[], abstained=True)
    context = "\n".join(
        f'<excerpt id="c{i}" source="{escape(names[ph.parent.doc_id])}">\n'
        f"{escape(ph.parent.text)}\n</excerpt>"
        for i, ph in enumerate(phits, 1)
    )
    prompt = load_prompt("query")
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
    cites = [citation_for(h, names[h.metadata["doc_id"]]) for h in hits]
    return QueryResponse(answer=res.text.strip(), citations=cites, abstained=False)
