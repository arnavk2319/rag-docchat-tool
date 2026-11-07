"""Model registry: embedder and reranker loaded once, shared by all requests."""

import logging
import re
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import Settings

log = logging.getLogger(__name__)


class FakeEmbedder:
    """Deterministic stand-in used when MODELS_FAKE=1 (tests)."""

    def encode(self, texts: list[str], **_: Any) -> list[list[float]]:
        return [[float(len(t) % 7), float(len(t) % 11), 1.0] for t in texts]


_WORD = re.compile(r"[a-z0-9]{3,}")


class FakeReranker:
    """Deterministic lexical stand-in: fraction of query words (3+ chars) found in the passage."""

    def predict(self, pairs: list[tuple[str, str]], **_: Any) -> list[float]:
        out: list[float] = []
        for q, p in pairs:
            qw = set(_WORD.findall(q.lower()))
            out.append(len(qw & set(_WORD.findall(p.lower()))) / len(qw) if qw else 0.0)
        return out


def _real_embedder(model_id: str) -> Any:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_id)


def _real_reranker(model_id: str) -> Any:
    from sentence_transformers import CrossEncoder

    return CrossEncoder(model_id)


@dataclass
class ModelRegistry:
    embedder: Any
    reranker: Any
    fake: bool = False


_lock = threading.Lock()
_registry: ModelRegistry | None = None


def check_fake_allowed(settings: Settings) -> bool:
    """MODELS_FAKE is honoured only under pytest; elsewhere it is a startup error."""
    if settings.models_fake and "pytest" not in sys.modules:
        raise RuntimeError("MODELS_FAKE is test-only; unset it outside the test suite")
    return settings.models_fake


def load_models(
    settings: Settings,
    embedder_loader: Callable[[str], Any] | None = None,
    reranker_loader: Callable[[str], Any] | None = None,
) -> ModelRegistry:
    """Load once; later calls return the same registry. MODELS_FAKE=1 uses fakes."""
    global _registry
    with _lock:
        if _registry is None:
            fake = check_fake_allowed(settings)
            e = embedder_loader or ((lambda _id: FakeEmbedder()) if fake else _real_embedder)
            r = reranker_loader or ((lambda _id: FakeReranker()) if fake else _real_reranker)
            log.info("loading models: %s, %s", settings.embed_model, settings.rerank_model)
            _registry = ModelRegistry(
                e(settings.embed_model),
                r(settings.rerank_model),
                fake=fake and embedder_loader is None and reranker_loader is None,
            )
        return _registry


def get_models() -> ModelRegistry:
    if _registry is None:
        raise RuntimeError("models not loaded (app lifespan has not run)")
    return _registry


def models_loaded() -> bool:
    return _registry is not None


def models_fake() -> bool:
    return _registry is not None and _registry.fake


def reset_models() -> None:
    """Test helper: drop the cached registry."""
    global _registry
    with _lock:
        _registry = None
