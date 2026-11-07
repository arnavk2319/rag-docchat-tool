"""Pure metric helpers and the threshold check (no I/O, no model calls)."""

from __future__ import annotations

import math
from collections.abc import Sequence

# (metric key, thresholds.json section, thresholds.json key, comparator)
RULES: list[tuple[str, str, str, str]] = [
    ("recall_at_5", "retrieval", "recall_at_5", ">="),
    ("mrr", "retrieval", "mrr", ">="),
    ("faithfulness", "answer", "faithfulness", ">="),
    ("relevance", "answer", "relevance", ">="),
    ("citation_correct", "answer", "citation_correct", ">="),
    ("task_success", "agent", "task_success", ">="),
    ("avg_steps", "agent", "max_avg_steps", "<="),
    ("injection_block_rate", "safety", "injection_block_rate", ">="),
    ("p95_latency_s", "performance", "p95_latency_s", "<="),
    ("max_usd_per_request", "performance", "max_usd_per_request", "<="),
]


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def percentile(xs: Sequence[float], p: float) -> float:
    """Nearest-rank percentile (p in 0..100)."""
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


def recall_at_k(ranked: Sequence[bool], n_relevant: int, k: int = 5) -> float:
    """ranked[i] = whether the i-th result is relevant; share of the relevant items found in top k."""
    return min(sum(ranked[:k]), n_relevant) / n_relevant if n_relevant else 0.0


def reciprocal_rank(ranked: Sequence[bool]) -> float:
    for i, r in enumerate(ranked, 1):
        if r:
            return 1.0 / i
    return 0.0


def check(metrics: dict[str, float], thresholds: dict[str, dict[str, float]]) -> list[dict]:
    """One row per thresholded metric: value, threshold, comparator, pass."""
    rows = []
    for key, section, tkey, op in RULES:
        thr = thresholds.get(section, {}).get(tkey)
        if thr is None or key not in metrics:
            continue
        v = metrics[key]
        ok = v >= thr if op == ">=" else v <= thr
        rows.append({"metric": key, "value": round(v, 4), "threshold": thr, "op": op, "pass": ok})
    return rows


def markdown_table(rows: list[dict], label: str) -> str:
    out = [f"| metric ({label}) | value | threshold | pass |", "| --- | --- | --- | --- |"]
    out += [f"| {r['metric']} | {r['value']} | {r['op']} {r['threshold']} | {'yes' if r['pass'] else 'NO'} |"
            for r in rows]
    return "\n".join(out)
