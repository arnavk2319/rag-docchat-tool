from __future__ import annotations

from typing import Any

from .base import ObjectStore
from .local import LocalObjectStore
from .s3 import S3ObjectStore, make_s3_client


def build_object_store(settings: Any) -> ObjectStore:
    """OBJECT_STORE=local (default, no credentials) or s3 (AWS S3 / Cloudflare R2)."""
    if settings.object_store == "s3":
        client = make_s3_client(
            settings.s3_endpoint,
            settings.s3_access_key_id.get_secret_value(),
            settings.s3_secret_access_key.get_secret_value(),
        )
        return S3ObjectStore(client, settings.s3_bucket)
    return LocalObjectStore(settings.object_store_path)
