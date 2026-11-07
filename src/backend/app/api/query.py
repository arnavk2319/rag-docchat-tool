"""POST /v1/query: single-shot hybrid retrieval + one `agent` LLM call, with citations.

`session_id` (T-020) loads bounded prior turns into the prompt as data and stores the turn after a completed answer. `stream=true` runs the agent and streams SSE
(T-019, ADR-0002 section 4); all 401/404/409/422 errors are plain JSON raised before it opens. `abstained` is true only when
retrieval returns nothing or the top rerank score is below MIN_RERANK_SCORE (no LLM call).
Pipeline: hybrid top-N -> parents -> cross-encoder rerank -> top_k parents -> LLM.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ..core import llm
from ..core.concurrency import run_blocking
from ..core.config import get_settings
from ..core.models import get_models
from ..core.prompts import load_prompt
from ..services.agent.tools import ToolContext
from ..services.parent_lookup import expand_to_parents
from ..services.rerank import pick_cited_children, rerank_parents
from ..services.retrieval import TOP_N, hybrid_search
from ..services.sessions import history_block
from .deps import (
    Bm25Dep,
    CurrentUser,
    ParentsDep,
    RegistryDep,
    SessionsDep,
    StoreDep,
    json_body_limit,
)
from .rag import citation_for, embed_one, escape, resolve_ready_docs
from .schemas import ErrorBody, QueryRequest, QueryResponse
from .sessions import not_found
from .sse import stream_agent

log = logging.getLogger("app.query")
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
    request: Request,
    body: QueryRequest,
    user: CurrentUser,
    registry: RegistryDep,
    store: StoreDep,
    parents: ParentsDep,
    bm25: Bm25Dep,
    sessions: SessionsDep,
) -> Any:
    sid = body.session_id
    hist: list[dict[str, str]] = []
    if sid is not None:  # validated before any stream opens; foreign == unknown -> 404
        msgs = await run_blocking(sessions.messages, user, sid)
        if msgs is None:
            raise not_found()
        hist = [{"role": m.role, "content": m.content} for m in msgs]
    if body.doc_ids is not None:
        docs = await resolve_ready_docs(registry, user, list(dict.fromkeys(body.doc_ids)))
    else:
        docs = [d for d in await run_blocking(registry.list_by_owner, user) if d.status == "ready"]
    names = {d.doc_id: d.filename for d in docs}
    if body.stream:
        return _sse_response(request, body, user, names, registry, store, parents, bm25, hist, sessions)
    if not names:
        return QueryResponse(answer=IDK, citations=[], abstained=True, session_id=sid)

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
        return QueryResponse(answer=IDK, citations=[], abstained=True, session_id=sid)

    # Candidates -> deduplicated parents -> cross-encoder rerank to top_k parents.
    cands = await run_blocking(expand_to_parents, user, hits, parents)
    ranked = await run_blocking(
        rerank_parents, get_models().reranker, body.question, cands, body.top_k
    )
    if not ranked or ranked[0][1] < get_settings().min_rerank_score:
        return QueryResponse(answer=IDK, citations=[], abstained=True, session_id=sid)
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
                "content": prompt.render(
                    context=context, question=history_block(hist) + escape(body.question)
                ),
            }
        ],
        model_role="agent",
        max_tokens=1024,
    )
    best = await run_blocking(
        pick_cited_children, get_models().reranker, body.question, phits, hits
    )
    cites = [citation_for(best[ph.parent.parent_id], names[ph.parent.doc_id]) for ph in phits]
    answer = res.text.strip()
    if sid is not None:
        try:  # memory is best-effort: a store fault must not fail a finished answer
            await run_blocking(
                sessions.append_turn, user, sid, body.question, answer, [c.chunk_id for c in cites]
            )
        except Exception:
            log.exception("session store failed", extra={"session_id": sid})
    return QueryResponse(answer=answer, citations=cites, abstained=False, session_id=sid)


def _embed_sync(text: str) -> list[float]:
    vecs = get_models().embedder.encode([text])
    return [float(x) for x in vecs[0]]


def _sse_response(
    request: Request, body: QueryRequest, user: str, names: dict[str, str],
    registry: Any, store: Any, parents: Any, bm25: Any, hist: list[dict[str, str]],
    sessions: Any,
) -> StreamingResponse:
    ctx = ToolContext(
        owner_id=user, doc_ids=list(names), registry=registry, vectors=store, parents=parents,
        bm25=bm25, embed=_embed_sync, reranker=get_models().reranker,
    )
    sid = body.session_id

    async def on_complete(answer: str, cite_ids: list[str]) -> None:
        if sid is not None:
            await run_blocking(sessions.append_turn, user, sid, body.question, answer, cite_ids)

    return StreamingResponse(
        stream_agent(request, body.question, ctx, names, history=hist, session_id=sid,
                     on_complete=on_complete),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
