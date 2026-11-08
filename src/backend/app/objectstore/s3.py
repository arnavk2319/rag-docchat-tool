"""S3/R2-compatible ObjectStore (boto3 client injected; sync calls wrapped in to_thread)."""

from __future__ import annotations

import asyncio
from typing import Any

from .base import ObjectStoreError, validate_key


def make_s3_client(endpoint: str, access_key_id: str, secret_access_key: str) -> Any:
    import boto3  # imported lazily: only needed when OBJECT_STORE=s3

    return boto3.client(
        "s3",
        endpoint_url=endpoint or None,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
    )


def _code(e: Exception) -> str:
    return str(getattr(e, "response", {}).get("Error", {}).get("Code", ""))


class S3ObjectStore:
    def __init__(self, client: Any, bucket: str) -> None:
        if not bucket:
            raise ObjectStoreError("S3_BUCKET is required for OBJECT_STORE=s3")
        self._c, self._bucket = client, bucket

    async def put(self, tenant_id: str, key: str, data: bytes) -> None:
        validate_key(tenant_id, key)
        await asyncio.to_thread(self._c.put_object, Bucket=self._bucket, Key=key, Body=data)

    async def get(self, tenant_id: str, key: str) -> bytes | None:
        validate_key(tenant_id, key)

        def _g() -> bytes | None:
            try:
                return self._c.get_object(Bucket=self._bucket, Key=key)["Body"].read()
            except Exception as e:
                if _code(e) in ("NoSuchKey", "404", "NotFound"):
                    return None
                raise

        return await asyncio.to_thread(_g)

    async def delete(self, tenant_id: str, key: str) -> None:
        validate_key(tenant_id, key)
        await asyncio.to_thread(self._c.delete_object, Bucket=self._bucket, Key=key)

    async def exists(self, tenant_id: str, key: str) -> bool:
        validate_key(tenant_id, key)

        def _h() -> bool:
            try:
                self._c.head_object(Bucket=self._bucket, Key=key)
                return True
            except Exception as e:
                if _code(e) in ("404", "NoSuchKey", "NotFound"):
                    return False
                raise

        return await asyncio.to_thread(_h)
