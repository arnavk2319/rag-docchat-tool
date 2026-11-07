"""Shared harness: the real documents router over a recording ingestion stub (no models)."""

import io
import zipfile
from datetime import UTC, datetime

from app.api import documents
from app.api.errors import install_error_handlers
from app.store.registry import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient

URL = "/v1/documents"
HDR = {"X-User-Id": "u1"}


class StubIngestion:
    """Records the temp path/mime it was given; behaviour injectable per test."""

    def __init__(self, behaviour=None):
        self.calls: list[tuple[str, str, str]] = []
        self.behaviour = behaviour

    async def ingest(self, owner_id, path, filename, mime, size=None):
        self.calls.append((path, mime, filename))
        if self.behaviour:
            self.behaviour(path, mime)
        return Document(
            doc_id="d" + str(len(self.calls)),
            owner_id=owner_id,
            filename=filename,
            mime=mime,
            size=size or 0,
            sha256="x",
            pages_or_sheets=1,
            created_at=datetime.now(UTC),
            status="ready",
        )


class StubRegistry:
    def get_by_sha256(self, owner_id, sha):
        return None


def make_client(stub: StubIngestion) -> TestClient:
    from app.api.deps import get_ingestion, get_registry

    app = FastAPI()
    app.include_router(documents.router)
    install_error_handlers(app)
    app.dependency_overrides[get_ingestion] = lambda: stub
    app.dependency_overrides[get_registry] = lambda: StubRegistry()
    return TestClient(app, raise_server_exceptions=False)


def post(client, name, data):
    return client.post(URL, files={"files": (name, data)}, headers=HDR)


def first(resp):
    return resp.json()["results"][0]


def zip_bytes(member: str, body: str = "x") -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(member, body)
    return b.getvalue()
