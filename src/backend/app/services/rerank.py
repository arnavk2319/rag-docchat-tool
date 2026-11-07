"""Cross-encoder rerank over parent chunks (reranker comes from the model registry)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.services.parent_lookup import ParentHit
from app.store.chroma import Hit


def rerank_parents(
    reranker: Any, question: str, phits: Sequence[ParentHit], top_k: int = 5
) -> list[tuple[ParentHit, float]]:
    """Score (question, parent text) pairs; return the best top_k as (hit, score), best first.

    Ties keep the incoming order (stable sort). Independent of child distances, so BM25-only
    hits (placeholder distance 1.0) are scored on text like any other."""
    if not phits:
        return []
    scores = reranker.predict([(question, ph.parent.text) for ph in phits])
    ranked = sorted(zip(phits, (float(s) for s in scores), strict=True), key=lambda t: -t[1])
    return ranked[:top_k]


def pick_cited_children(
    reranker: Any, question: str, phits: Sequence[ParentHit], hits: Sequence[Hit]
) -> dict[str, Hit]:
    """For each kept parent, the hit child that best matches the question (parent_id -> Hit).

    One batched predict over children of parents with 2+ hit children; ties keep hybrid order.
    A parent with a single hit child needs no scoring."""
    by_parent: dict[str, list[Hit]] = {}
    for h in hits:
        by_parent.setdefault(str(h.metadata.get("parent_id", "")), []).append(h)
    by_id = {h.chunk_id: h for h in hits}
    out: dict[str, Hit] = {}
    multi: list[Hit] = []
    for ph in phits:
        kids = by_parent.get(ph.parent.parent_id) or [by_id[ph.best_child_id]]
        if len(kids) == 1:
            out[ph.parent.parent_id] = kids[0]
        else:
            multi.extend(kids)
    if multi:
        scores = reranker.predict([(question, h.text) for h in multi])
        best: dict[str, tuple[float, Hit]] = {}
        for h, sc in zip(multi, scores, strict=True):
            pid = str(h.metadata["parent_id"])
            if pid not in best or float(sc) > best[pid][0]:
                best[pid] = (float(sc), h)
        out.update({pid: h for pid, (_, h) in best.items()})
    return out
