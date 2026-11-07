"""Allow-listed agent tools: search_documents, list_documents, get_page_or_section.

Every tool runs in a ToolContext (owner_id + allowed doc_ids) and goes through services only.
Out-of-scope or unknown doc ids give the same generic "not found" (no existence leak). Results
are untrusted data: text is escaped, wrapped in <tool_result>...</tool_result> and size-bounded.
Tools are synchronous; the agent loop calls them via run_blocking.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ...core import llm
from ...core.prompts import load_prompt
from ..parent_lookup import expand_to_parents
from ..rerank import pick_cited_children, rerank_parents
from ..retrieval import TOP_N, hybrid_search
from ..summarization import DocText, summarize_documents

MAX_RESULT_CHARS = 6000
MAX_SNIPPET_CHARS = 1200
MAX_LIST_DOCS = 50
MAX_TOP_K = 8
MAX_COMPARE_DOCS = 5
COMPARE_PER_DOC = 3
COMPARE_EXCERPT_CHARS = 1000
NOT_FOUND = "not found"
_ESC = str.maketrans({"<": "&lt;", ">": "&gt;", "&": "&amp;"})


@dataclass
class ToolContext:
    owner_id: str
    doc_ids: Sequence[str] | None  # allowed doc scope; None = all of the owner's documents
    registry: Any
    vectors: Any
    parents: Any
    bm25: Any
    embed: Callable[[str], Sequence[float]]
    reranker: Any
    min_rerank_score: float = 0.0


def escape(text: str) -> str:
    """Neutralise angle brackets (and &) so data cannot forge or close a delimiter."""
    return text.translate(_ESC)


def wrap(body: str, ok: bool = True) -> str:
    """Escape, bound and wrap a tool result in <tool_result> delimiters."""
    body = escape(body)
    cap = MAX_RESULT_CHARS
    if len(body) > cap:
        body = body[:cap] + "\n[truncated]"
    status = "ok" if ok else "error"
    return f'<tool_result status="{status}">\n{body}\n</tool_result>'


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchArgs(_Args):
    query: str = Field(min_length=1, max_length=500)
    doc_ids: list[str] | None = Field(default=None, max_length=50)
    top_k: int = Field(default=4, ge=1, le=MAX_TOP_K)


class ListArgs(_Args):
    pass


class GetPageArgs(_Args):
    doc_id: str = Field(min_length=1, max_length=100)
    page: int | None = Field(default=None, ge=1)
    section: str | None = Field(default=None, min_length=1, max_length=200)


class SummarizeArgs(_Args):
    doc_id: str = Field(min_length=1, max_length=100)


class CompareArgs(_Args):
    doc_ids: list[str] = Field(min_length=2, max_length=MAX_COMPARE_DOCS)
    focus: str = Field(default="main points", min_length=1, max_length=300)


def _visible(ctx: ToolContext, doc_id: str) -> Any | None:
    if ctx.doc_ids is not None and doc_id not in ctx.doc_ids:
        return None
    return ctx.registry.get(ctx.owner_id, doc_id)


def _search(ctx: ToolContext, a: SearchArgs) -> str:
    allowed = [
        d.doc_id
        for d in ctx.registry.list_by_owner(ctx.owner_id)
        if d.status == "ready" and (ctx.doc_ids is None or d.doc_id in ctx.doc_ids)
    ]
    names = {d.doc_id: d.filename for d in ctx.registry.list_by_owner(ctx.owner_id)}
    if a.doc_ids is not None:
        scope = set(allowed)
        if any(i not in scope for i in a.doc_ids):
            return wrap(NOT_FOUND, ok=False)
        allowed = list(dict.fromkeys(a.doc_ids))
    if not allowed:
        return wrap("no results")
    hits = hybrid_search(
        ctx.owner_id, a.query, ctx.embed(a.query), ctx.vectors, ctx.bm25, allowed,
        max(a.top_k, TOP_N),
    )
    cands = expand_to_parents(ctx.owner_id, hits, ctx.parents)
    ranked = rerank_parents(ctx.reranker, a.query, cands, a.top_k)
    if not ranked or ranked[0][1] < ctx.min_rerank_score:
        return wrap("no results")
    phits = [ph for ph, _ in ranked]
    best = pick_cited_children(ctx.reranker, a.query, phits, hits)
    out = []
    for ph, score in ranked:
        p = ph.parent
        h = best.get(p.parent_id)
        loc = f"page {p.page}" if p.page is not None else (f"sheet {p.sheet}" if p.sheet else "")
        snippet = (h.text if h else p.text)[:MAX_SNIPPET_CHARS]
        out.append(
            f'[doc_id={p.doc_id} file={names.get(p.doc_id, "")} {loc} '
            f'section={p.section} score={score:.3f}]\n{snippet}'
        )
    return wrap("\n---\n".join(out))


def _list(ctx: ToolContext, a: ListArgs) -> str:
    docs = [
        d for d in ctx.registry.list_by_owner(ctx.owner_id)
        if ctx.doc_ids is None or d.doc_id in ctx.doc_ids
    ]
    if not docs:
        return wrap("no documents")
    lines = [
        f"doc_id={d.doc_id} file={d.filename} pages_or_sheets={d.pages_or_sheets} "
        f"status={d.status}"
        for d in docs[:MAX_LIST_DOCS]
    ]
    if len(docs) > MAX_LIST_DOCS:
        lines.append(f"[{len(docs) - MAX_LIST_DOCS} more not shown]")
    return wrap("\n".join(lines))


def _get_page(ctx: ToolContext, a: GetPageArgs) -> str:
    doc = _visible(ctx, a.doc_id)
    if doc is None or doc.status != "ready":
        return wrap(NOT_FOUND, ok=False)
    ps = ctx.parents.list_doc_parents(ctx.owner_id, a.doc_id)
    if a.page is not None:
        ps = [p for p in ps if p.page == a.page]
    if a.section is not None:
        want = a.section.casefold()
        ps = [p for p in ps if want in (p.section or "").casefold() or want == (p.sheet or "").casefold()]
    if not ps:
        return wrap(NOT_FOUND, ok=False)
    return wrap("\n\n".join(p.text for p in ps))


def _run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine from a sync tool (worker thread); use a helper thread if a loop is running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


def _summarize(ctx: ToolContext, a: SummarizeArgs) -> str:
    doc = _visible(ctx, a.doc_id)
    if doc is None or doc.status != "ready":
        return wrap(NOT_FOUND, ok=False)
    ps = ctx.parents.list_doc_parents(ctx.owner_id, a.doc_id)
    res = _run_async(summarize_documents([DocText(doc.filename, [p.text for p in ps])]))
    note = " (partial: cost cap reached)" if res.partial else ""
    return wrap(f"[doc_id={a.doc_id}]{note}\n{res.summary}")


def _compare(ctx: ToolContext, a: CompareArgs) -> str:
    ids = list(dict.fromkeys(a.doc_ids))
    docs = [_visible(ctx, i) for i in ids]
    if len(ids) < 2 or any(d is None or d.status != "ready" for d in docs):
        return wrap(NOT_FOUND, ok=False)
    emb = ctx.embed(a.focus)
    blocks: list[str] = []
    for d in docs:
        hits = hybrid_search(
            ctx.owner_id, a.focus, emb, ctx.vectors, ctx.bm25, [d.doc_id], COMPARE_PER_DOC
        )
        cands = expand_to_parents(ctx.owner_id, hits, ctx.parents)
        ranked = rerank_parents(ctx.reranker, a.focus, cands, COMPARE_PER_DOC)
        text = "\n".join(ph.parent.text[:COMPARE_EXCERPT_CHARS] for ph, _ in ranked)
        blocks.append(f'<excerpt doc_id="{d.doc_id}" file="{escape(d.filename)}">\n'
                      f"{escape(text) or '(no relevant text)'}\n</excerpt>")
    prompt = load_prompt("compare").render(focus=escape(a.focus), excerpts="\n".join(blocks))
    res = _run_async(llm.complete(
        [{"role": "user", "content": prompt}], model_role="fast", max_tokens=500))
    cites = " ".join(f"[doc_id={d.doc_id}]" for d in docs)
    return wrap(f"{res.text.strip()}\nSources: {cites}")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[_Args]
    run: Callable[[ToolContext, Any], str]


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool("search_documents", "Hybrid search over the user's documents.", SearchArgs, _search),
        Tool("list_documents", "List the user's documents.", ListArgs, _list),
        Tool("get_page_or_section", "Fetch a page or section of a document.", GetPageArgs, _get_page),
        Tool("summarize_document", "Summarize one document.", SummarizeArgs, _summarize),
        Tool("compare_documents", "Compare 2-5 documents.", CompareArgs, _compare),
    )
}


def run_tool(name: str, raw_args: Any, ctx: ToolContext) -> str:
    """Execute an allow-listed tool; never raises. Returns a wrapped <tool_result> string."""
    tool = TOOLS.get(name) if isinstance(name, str) else None
    if tool is None:
        return wrap("unknown tool", ok=False)
    try:
        args = tool.args_model.model_validate(raw_args if raw_args is not None else {})
    except ValidationError:
        return wrap("invalid arguments", ok=False)
    try:
        return tool.run(ctx, args)
    except Exception:  # noqa: BLE001 - tool failures must not leak internals to the model
        return wrap("tool failed", ok=False)
