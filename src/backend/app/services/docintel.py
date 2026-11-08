"""Per-document summary and sentiment with citations (T-030).

One `fast` llm-wrapper call per request over numbered, sanitized sections (document text is
untrusted data). The model returns section indexes; citations are built from OUR parent records
(never from model-written ids), so they cannot be forged. Weak/empty documents abstain without
an LLM call.
"""

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..core import llm
from ..core.config import get_settings
from ..core.llm import Budget, LLMValidationError
from ..core.prompts import load_prompt
from ..core.security import sanitize_untrusted
from ..store.parents import Parent

IDK = "I don't know."
MIN_WORDS = 8  # fewer words than this is too weak to summarize or judge
MAX_CHARS = 16_000  # prompt cap on document text
MAX_CITES = 5
LABELS = ("positive", "negative", "neutral", "mixed")
Label = Literal["positive", "negative", "neutral", "mixed", "unclear"]


class SummaryOut(BaseModel):
    summary: str = ""
    sections: list[Any] = Field(default_factory=list)
    insufficient: bool = False


class SentimentOut(BaseModel):
    label: str = "unclear"
    confidence: float = 0.0
    rationale: str = ""
    sections: list[Any] = Field(default_factory=list)
    insufficient: bool = False


class DocIntelResult(BaseModel):
    abstained: bool
    partial: bool = False
    summary: str = ""
    label: Label = "unclear"
    confidence: float = 0.0
    rationale: str = ""
    cited: list[Parent] = Field(default_factory=list)


def _weak(parents: Sequence[Parent]) -> bool:
    return sum(len(p.text.split()) for p in parents) < MIN_WORDS


def _pack(parents: Sequence[Parent]) -> tuple[list[Parent], str, bool]:
    used: list[Parent] = []
    lines: list[str] = []
    size = 0
    partial = False
    for p in parents:
        room = MAX_CHARS - size
        if room <= 0:
            partial = True
            break
        text = p.text[:room]
        partial = partial or len(text) < len(p.text)
        lines.append(f'<section index="{len(used)}">{sanitize_untrusted(text)}</section>')
        used.append(p)
        size += len(text)
    return used, "\n".join(lines), partial


def _pick(used: Sequence[Parent], idx: Sequence[Any]) -> list[Parent]:
    seen: list[int] = []
    for i in idx:
        if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(used) and i not in seen:
            seen.append(i)
    if not seen:  # model cited nothing valid: fall back to the leading sections it was shown
        seen = list(range(min(MAX_CITES, len(used))))
    return [used[i] for i in seen[:MAX_CITES]]


async def _call[T: BaseModel](prompt_id: str, sections: str, schema: type[T], budget: Budget) -> T:
    res = await llm.complete(
        [{"role": "user", "content": load_prompt(prompt_id).render(sections=sections)}],
        model_role="fast", schema=schema, max_tokens=500, budget=budget,
    )
    if not isinstance(res.parsed, schema):
        raise LLMValidationError("unexpected structured output")
    return res.parsed


def _budget() -> Budget:
    return Budget(max_steps=1, max_tokens=1_000_000, max_usd=get_settings().summary_max_usd)


async def summarize_doc(parents: Sequence[Parent], budget_out: list[Budget] | None = None) -> DocIntelResult:
    if _weak(parents):
        return DocIntelResult(abstained=True, summary=IDK)
    used, text, partial = _pack(parents)
    budget = _budget()
    if budget_out is not None:
        budget_out.append(budget)
    out = await _call("summary", text, SummaryOut, budget)
    if out.insufficient or not out.summary.strip():
        return DocIntelResult(abstained=True, summary=IDK)
    return DocIntelResult(abstained=False, partial=partial, summary=out.summary.strip(),
                          cited=_pick(used, out.sections))


async def classify_doc(parents: Sequence[Parent], budget_out: list[Budget] | None = None) -> DocIntelResult:
    if _weak(parents):
        return DocIntelResult(abstained=True, rationale=IDK)
    used, text, partial = _pack(parents)
    budget = _budget()
    if budget_out is not None:
        budget_out.append(budget)
    out = await _call("sentiment", text, SentimentOut, budget)
    label = out.label.strip().lower()
    if out.insufficient or label not in LABELS:
        return DocIntelResult(abstained=True, rationale=IDK)
    return DocIntelResult(
        abstained=False, partial=partial, label=label, confidence=min(1.0, max(0.0, out.confidence)),  # type: ignore[arg-type]
        rationale=out.rationale.strip(), cited=_pick(used, out.sections),
    )
