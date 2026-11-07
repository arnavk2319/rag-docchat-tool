"""Test helpers: full-width vectors and a sync view over an in-memory VectorStore."""

from app.vectorstore import InMemoryVectorStore, SyncVectors
from app.vectorstore.base import EMBEDDING_DIM


def vec(*head: float) -> list[float]:
    """Pad a short vector with zeros to the VectorStore width (cosine unchanged)."""
    return [float(x) for x in head] + [0.0] * (EMBEDDING_DIM - len(head))


V111 = vec(1, 1, 1)


def make_store() -> SyncVectors:
    return SyncVectors(InMemoryVectorStore())
