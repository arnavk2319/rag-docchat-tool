"""Map child query hits to their (deduplicated) parent chunks."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from app.store.parents import Parent, SqliteParents
from app.vectorstore.sync import Hit


class ParentHit(BaseModel):
    parent: Parent
    best_child_id: str
    distance: float  # best (smallest) child distance
    score: float  # 1 - distance (cosine similarity)


def expand_to_parents(
    owner_id: str, child_hits: Sequence[Hit], parents: SqliteParents
) -> list[ParentHit]:
    """One entry per parent, ordered by the best child hit (input order), owner-scoped.

    Hits without parent_id metadata or whose parent is missing are skipped."""
    best: dict[str, Hit] = {}
    for h in child_hits:
        pid = h.metadata.get("parent_id")
        if not pid:
            continue
        if pid not in best or h.distance < best[pid].distance:
            best[pid] = h
    order = sorted(best, key=lambda p: best[p].distance)  # stable: ties keep first-seen order
    out: list[ParentHit] = []
    for p in parents.get_parents(owner_id, order):
        h = best[p.parent_id]
        out.append(
            ParentHit(parent=p, best_child_id=h.chunk_id, distance=h.distance, score=1 - h.distance)
        )
    return out
