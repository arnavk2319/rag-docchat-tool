"""Map-reduce summarization with a content cache, a USD cap and optional compression.

map: section batches -> short partial summaries (`fast`); reduce: partials -> final summary
(role from SUMMARY_REDUCE_ROLE). Cache key = sha256(texts, prompt versions, reduce role, compress
flag): a repeat call makes 0 LLM calls. The per-summary `Budget` stops the run at
SUMMARY_MAX_USD and returns whatever was produced with `partial=True` (never cached).
"""

import hashlib
import logging
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..core import llm
from ..core.config import get_settings
from ..core.llm import Budget, BudgetExceeded, LLMError
from ..core.prompts import load_prompt
from ..core.security import sanitize_untrusted
from . import compression

log = logging.getLogger("app.summarization")
_CACHE_MAX = 128
_cache: OrderedDict[str, str] = OrderedDict()


@dataclass(frozen=True)
class DocText:
    name: str
    sections: Sequence[str]  # parent texts in document order


@dataclass
class SummaryResult:
    summary: str
    partial: bool = False
    cached: bool = False
    llm_calls: int = 0
    usd: float = 0.0
    tokens: int = 0


def clear_cache() -> None:
    _cache.clear()


def make_batches(sections: Sequence[str], max_chars: int) -> list[str]:
    """Greedy-pack consecutive sections into batches of <= max_chars (long sections are split)."""
    batches: list[str] = []
    cur = ""
    for sec in sections:
        sec = sec.strip()
        while sec:
            room = max_chars - len(cur) - (1 if cur else 0)
            if room <= 0:
                batches.append(cur)
                cur = ""
                continue
            piece, sec = sec[:room], sec[room:]
            cur = f"{cur}\n{piece}" if cur else piece
            if sec:
                batches.append(cur)
                cur = ""
    if cur:
        batches.append(cur)
    return batches


def _cache_key(docs: Sequence[DocText], role: str, compress: bool, batch_chars: int) -> str:
    h = hashlib.sha256()
    versions = [load_prompt(n).version for n in ("summarize_map", "summarize_reduce", "compress")]
    h.update(f"{versions}|{role}|{compress}|{batch_chars}".encode())
    for d in docs:
        h.update(b"\x01" + d.name.encode())
        for s in d.sections:
            h.update(b"\x02" + s.encode())
    return h.hexdigest()


def _remember(key: str, summary: str) -> None:
    _cache[key] = summary
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


async def summarize_documents(
    docs: Sequence[DocText], max_usd: float | None = None, cancel: Any = None
) -> SummaryResult:
    s = get_settings()
    key = _cache_key(docs, s.summary_reduce_role, s.summary_compress, s.summary_batch_chars)
    if key in _cache:
        _cache.move_to_end(key)
        return SummaryResult(_cache[key], cached=True)

    work: list[tuple[str, int, str]] = []  # (doc name, part number, text)
    truncated = False
    for d in docs:
        for i, b in enumerate(make_batches(d.sections, s.summary_batch_chars), 1):
            work.append((d.name, i, b))
    if len(work) > s.summary_max_batches:
        work, truncated = work[: s.summary_max_batches], True
    if not work:
        return SummaryResult("The documents contain no readable text.")

    budget = Budget(
        max_steps=3 * len(work) + 2 * s.summary_max_batches + 4,
        max_tokens=10_000_000,
        max_usd=s.summary_max_usd if max_usd is None else min(s.summary_max_usd, max_usd),
    )
    budget.cancel = cancel  # threading.Event | None: checked before every LLM call
    map_p, red_p = load_prompt("summarize_map"), load_prompt("summarize_reduce")
    partials: list[tuple[str, str]] = []
    partial_flag = truncated
    try:
        for name, part, text in work:
            if s.summary_compress:
                text = await compression.compress_text(text, "summarize the main points", budget=budget)
            res = await llm.complete(
                [{"role": "user", "content": map_p.render(
                    name=sanitize_untrusted(name), part=str(part), text=sanitize_untrusted(text))}],
                model_role="fast",
                max_tokens=300,
                budget=budget,
            )
            partials.append((name, res.text.strip()))
        summary = await _reduce(partials, red_p, s.summary_reduce_role, s.summary_batch_chars, budget)
    except (BudgetExceeded, LLMError) as e:
        if not partials:
            raise
        log.warning("summary partial", extra={"reason": type(e).__name__, "usd": round(budget.usd, 6)})
        partial_flag = True
        summary = "\n".join(p for _, p in partials) or "Summary unavailable: cost cap reached."
    if not partial_flag:
        _remember(key, summary)
    return SummaryResult(summary, partial=partial_flag, llm_calls=budget.steps, usd=budget.usd, tokens=budget.tokens)


async def _reduce(
    partials: list[tuple[str, str]], prompt, role, batch_chars: int, budget: Budget
) -> str:
    if len(partials) == 1:
        return partials[0][1]
    items = [f'<partial document="{sanitize_untrusted(n)}">{sanitize_untrusted(p)}</partial>' for n, p in partials]
    # Hierarchical collapse when the partials do not fit one reduce call.
    while sum(len(i) for i in items) > batch_chars and len(items) > 1:
        grouped, cur, size = [], [], 0
        for it in items:
            if cur and size + len(it) > batch_chars:
                grouped.append(cur)
                cur, size = [], 0
            cur.append(it)
            size += len(it)
        grouped.append(cur)
        if len(grouped) == len(items):  # cannot shrink further
            break
        items = []
        for g in grouped:
            out = await _reduce_call("\n".join(g), prompt, role, budget)
            items.append(f"<partial>{sanitize_untrusted(out)}</partial>")
    return await _reduce_call("\n".join(items), prompt, role, budget)


async def _reduce_call(partials: str, prompt, role, budget: Budget) -> str:
    res = await llm.complete(
        [{"role": "user", "content": prompt.render(partials=partials)}],
        model_role=role,
        max_tokens=512,
        budget=budget,
    )
    return res.text.strip()
