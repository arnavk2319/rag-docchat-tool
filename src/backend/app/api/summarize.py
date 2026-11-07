"""POST /v1/summarize: single-shot summary via the `fast` role (map-reduce comes in T-012)."""

from fastapi import APIRouter, Depends

from ..core import llm
from ..core.prompts import load_prompt
from .deps import CurrentUser, ParentsDep, RegistryDep, json_body_limit
from .rag import SUMMARY_MAX_CHARS, doc_text, escape, resolve_ready_docs
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
    per_doc = SUMMARY_MAX_CHARS // len(docs)
    blocks = []
    for d in docs:
        text = await doc_text(parents, user, d.doc_id, per_doc)
        blocks.append(f'<document name="{escape(d.filename)}">\n{escape(text)}\n</document>')
    prompt = load_prompt("summarize")
    res = await llm.complete(
        [{"role": "user", "content": prompt.render(documents="\n".join(blocks))}],
        model_role="fast",
        max_tokens=512,
    )
    return SummarizeResponse(summary=res.text.strip(), doc_ids=ids)
