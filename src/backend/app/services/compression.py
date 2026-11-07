"""Contextual compression: shrink passages to what matters for a focus (LLM, extractive fallback).

Replaces the legacy `utilities/contextual_compressor.py`. Pure functions plus a small in-memory
cache keyed by sha256(text, focus, prompt version): a cache hit costs no LLM call.
"""

import hashlib
import re
from collections import OrderedDict
from collections.abc import Sequence

from ..core import llm
from ..core.llm import Budget, BudgetExceeded, LLMError
from ..core.prompts import load_prompt

_SENT = re.compile(r"(?<=[.!?])\s+|\n{2,}")
_WORD = re.compile(r"[a-z0-9]{3,}")
_CACHE_MAX = 512
_cache: OrderedDict[str, str] = OrderedDict()


def clear_cache() -> None:
    _cache.clear()


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text) if s and s.strip()]


def extractive_compress(text: str, focus: str = "", n_sentences: int = 3) -> str:
    """Top-n sentences by word-frequency (+ focus-word overlap) score, in original order.

    Fallback text when there are no scorable sentences: the first 300 characters of the input."""
    text = text.strip()
    if n_sentences <= 0 or not text:
        return ""
    sents = split_sentences(text)
    if not sents:
        return text[:300]
    if len(sents) <= n_sentences:
        return " ".join(sents)
    freq: dict[str, int] = {}
    for s in sents:
        for w in _WORD.findall(s.lower()):
            freq[w] = freq.get(w, 0) + 1
    focus_words = set(_WORD.findall(focus.lower()))

    def score(s: str) -> float:
        words = _WORD.findall(s.lower())
        if not words:
            return 0.0
        base = sum(freq[w] for w in words) / len(words)
        return base + 5.0 * len(focus_words.intersection(words))

    ranked = sorted(range(len(sents)), key=lambda i: (-score(sents[i]), i))[:n_sentences]
    return " ".join(sents[i] for i in sorted(ranked))


def _key(text: str, focus: str, version: int) -> str:
    h = hashlib.sha256()
    for part in (str(version), focus, text):
        h.update(part.encode())
        h.update(b"\x00")
    return h.hexdigest()


def _escape(text: str) -> str:
    return text.replace("<", "&lt;").replace(">", "&gt;")


async def compress_text(
    text: str,
    focus: str = "summarize the main points",
    *,
    budget: Budget | None = None,
    n_sentences: int = 3,
) -> str:
    """LLM compression through the wrapper (`fast`). Falls back to extractive on empty output or an
    LLM error; BudgetExceeded propagates so the caller can stop. Never longer than the input."""
    if not text.strip():
        return ""
    prompt = load_prompt("compress")
    key = _key(text, focus, prompt.version)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    try:
        res = await llm.complete(
            [{"role": "user", "content": prompt.render(focus=_escape(focus), text=_escape(text))}],
            model_role="fast",
            max_tokens=max(64, len(text) // 8),
            budget=budget,
        )
        out = res.text.strip()
    except BudgetExceeded:
        raise
    except LLMError:
        out = ""
    if not out or out.upper() == "NONE" or len(out) > len(text):
        out = extractive_compress(text, focus, n_sentences)
    _cache[key] = out
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return out


async def compress_passages(
    focus: str, passages: Sequence[str], *, budget: Budget | None = None
) -> list[str]:
    """One compressed string per input passage, same order (no duplicates, no reordering)."""
    return [await compress_text(p, focus, budget=budget) for p in passages]
