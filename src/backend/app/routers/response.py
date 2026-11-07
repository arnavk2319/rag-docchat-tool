"""Upload validation router. Importable without langchain/model packages."""

import io
import os
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..core.concurrency import run_blocking
from ..utilities.file_handler import ParseError, parse_uploaded_file
from ..utilities.logging_module import logger

router = APIRouter()
FILE_FIELD = File(...)

MAX_FILE_SIZE = 10 * 1024 * 1024
CHUNK_SIZE = 64 * 1024

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# extension -> MIME types the sniffed content may have
EXT_TO_MIMES: dict[str, set[str]] = {
    ".pdf": {"application/pdf"},
    ".docx": {DOCX},
    ".txt": {"text/plain"},
    ".csv": {"text/csv", "text/plain"},
    ".xls": {"application/vnd.ms-excel"},
    ".xlsx": {XLSX},
}
ALLOWED_EXTENSIONS = sorted(EXT_TO_MIMES)
ALLOWED_MIME_TYPES = sorted({m for v in EXT_TO_MIMES.values() for m in v})


MAX_ZIP_MEMBERS = 1000
MAX_ZIP_UNCOMPRESSED = 200 * 1024 * 1024


def _sniff_zip(data: bytes) -> str | None:
    """Classify an OOXML zip from member names only; nothing is extracted."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
    except (zipfile.BadZipFile, ValueError, NotImplementedError):
        return None
    if len(infos) > MAX_ZIP_MEMBERS or sum(i.file_size for i in infos) > MAX_ZIP_UNCOMPRESSED:
        return None
    names = [i.filename for i in infos]
    if any(n.startswith("word/") for n in names):
        return DOCX
    if any(n.startswith("xl/") for n in names):
        return XLSX
    return None


def sniff_mime(data: bytes, ext: str) -> str | None:
    """Detect MIME from the full (size-capped) content. Text has no magic bytes,
    so text-like content is classed by extension (csv/txt) once it decodes as
    UTF-8 without NULs."""
    head = data[:8192]
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"PK\x03\x04"):
        return _sniff_zip(data)
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "application/vnd.ms-excel" if ext == ".xls" else None
    if b"\x00" not in head:
        try:
            head.decode("utf-8")
        except UnicodeDecodeError as e:
            # a multibyte char may be cut at the end of the sample
            if e.start < len(head) - 4:
                return None
        return "text/csv" if ext == ".csv" else "text/plain"
    return None


async def read_limited(file: UploadFile, limit: int | None = None) -> bytes:
    """Read the upload in chunks, rejecting as soon as it exceeds `limit`."""
    limit = MAX_FILE_SIZE if limit is None else limit
    buf = bytearray()
    while chunk := await file.read(CHUNK_SIZE):
        buf.extend(chunk)
        if len(buf) > limit:
            raise HTTPException(status_code=413, detail="File is too large")
    return bytes(buf)


def parse_document(path: str, mime_type: str) -> list[dict]:
    """Parse to JSON-friendly page/sheet-tagged text; raises ParseError."""
    return [asdict(p) for p in parse_uploaded_file(path, mime_type)]


def summarize_document(parsed) -> str | None:
    """Summary hook; replaced by the llm wrapper in later tickets."""
    return None


@router.get("/ping-checkpoint", tags=["ping-checkpoint"])
def ping_checkpoint():
    logger.info("Checking endpoint status")
    return "Returning OK 200"


@router.post("/validate-upload", tags=["upload"])
async def upload_file(file: UploadFile = FILE_FIELD):
    tmp_path: str | None = None
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="Filename is required")
        ext = Path(file.filename).suffix.lower()
        if ext not in EXT_TO_MIMES:
            raise HTTPException(status_code=400, detail=f"File extension not allowed: {ext or 'none'}")

        contents = await read_limited(file)
        if not contents:
            raise HTTPException(status_code=400, detail="File is empty")

        mime_type = sniff_mime(contents, ext)
        if mime_type is None or mime_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(status_code=400, detail="File content type not allowed")
        if mime_type not in EXT_TO_MIMES[ext]:
            raise HTTPException(status_code=400, detail="File extension does not match content")

        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp_path = tmp.name
            tmp.write(contents)

        parsed = await run_blocking(parse_document, tmp_path, mime_type)
        if parsed is None:
            raise HTTPException(status_code=422, detail="File could not be parsed")
        logger.info("Parsed upload %s (%s)", ext, mime_type)
        return {"filename": file.filename, "content": parsed, "summary": summarize_document(parsed)}
    except HTTPException:
        raise
    except ParseError as e:
        logger.warning("Upload parse failed: %s", e)
        raise HTTPException(status_code=422, detail="File could not be parsed") from None
    except Exception:
        logger.exception("Unexpected error on upload")
        raise HTTPException(status_code=500, detail="Internal error while processing upload") from None
    finally:
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass
