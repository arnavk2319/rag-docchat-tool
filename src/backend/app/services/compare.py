"""Cross-document comparison with per-document citations (T-031).

One `fast` llm-wrapper call over numbered, sanitized sections grouped per document (document
text is untrusted data). The model returns section indexes only; citations are built from OUR
parent records, so they cannot be forged or cross-attributed. Weak evidence abstains without an
LLM call.
"""

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

from ..core import llm
from ..core.config import get_settings
from ..core.llm import Budget, LLMValidationError
from ..core.prompts import load_prompt
from ..core.security import sanitize_untrusted
from ..store.parents import Parent
from .docintel import MIN_WORDS

MAX_DOCS = 5
TOTAL_CHARS = 20_000  # prompt cap on document text across all documents, split evenly
MAX_POINTS = 5
MAX_CITES_PER_POINT = 6


class PointOut(BaseModel):
    point: str = ""
    sections: list[Any] = Field(default_factory=list)


class CompareOut(BaseModel):
    similarities: list[PointOut] = Field(default_factory=list)
    differences: list[PointOut] = Field(default_factory=list)
    insufficient: bool = False


class Point(BaseModel):
    point: str
    cited: list[Parent]


class CompareResult(BaseModel):
    abstained: bool
    partial: bool = False
    similarities: list[Point] = Field(default_factory=list)
    differences: list[Point] = Field(default_factory=list)


def _weak(per_doc: Sequence[Sequence[Parent]]) -> bool:
    return any(sum(len(p.text.split()) for p in ps) < MIN_WORDS for ps in per_doc)


def _pack(per_doc: Sequence[tuple[str, Sequence[Parent]]]) -> tuple[list[Parent], str, bool]:
    """Numbered sections, grouped by document; each document gets an equal share of the cap."""
    share = TOTAL_CHARS // max(1, len(per_doc))
    used: list[Parent] = []
    blocks: list[str] = []
    partial = False
    for doc_id, parents in per_doc:
        size = 0
        lines: list[str] = []
        for p in parents:
            room = share - size
            if room <= 0:
                partial = True
                break
            text = p.text[:room]
            partial = partial or len(text) < len(p.text)
            lines.append(f'<section index="{len(used)}">{sanitize_untrusted(text)}</section>')
            used.append(p)
            size += len(text)
        blocks.append(f'<document id="{len(blocks) + 1}">\n' + "\n".join(lines) + "\n</document>")
    return used, "\n".join(blocks), partial


def _points(items: Sequence[PointOut], used: Sequence[Parent]) -> list[Point]:
    out: list[Point] = []
    for it in items[:MAX_POINTS]:
        text = it.point.strip()
        if not text:
            continue
        seen: list[int] = []
        for i in it.sections:
            if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(used) and i not in seen:
                seen.append(i)
        if seen:  # a point citing nothing valid is ungrounded: drop it
            out.append(Point(point=text, cited=[used[i] for i in seen[:MAX_CITES_PER_POINT]]))
    return out


def _budget() -> Budget:
    return Budget(max_steps=1, max_tokens=1_000_000, max_usd=get_settings().summary_max_usd)


async def compare_docs(
    per_doc: Sequence[tuple[str, Sequence[Parent]]], focus: str, budget_out: list[Budget] | None = None,
) -> CompareResult:
    if _weak([ps for _, ps in per_doc]):
        return CompareResult(abstained=True)
    used, text, partial = _pack(per_doc)
    budget = _budget()
    if budget_out is not None:
        budget_out.append(budget)
    res = await llm.complete(
        [{"role": "user", "content": load_prompt("compare_docs").render(
            focus=sanitize_untrusted(focus, 200) or "overall content", documents=text)}],
        model_role="fast", schema=CompareOut, max_tokens=800, budget=budget,
    )
    if not isinstance(res.parsed, CompareOut):
        raise LLMValidationError("unexpected structured output")
    out = res.parsed
    sims, diffs = _points(out.similarities, used), _points(out.differences, used)
    if out.insufficient or not (sims or diffs):
        return CompareResult(abstained=True)
    return CompareResult(abstained=False, partial=partial, similarities=sims, differences=diffs)

