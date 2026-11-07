"""Security helpers: prompt-injection sanitising of untrusted text (see the second half of this
module and docs/architecture/security.md) and upload content security.

Upload content security: extension allowlist, MIME sniffing, size and zip limits.

Moved unchanged in behaviour from the legacy upload router (ADR-0002 section 7). Pure
functions plus UploadRejected; no FastAPI response objects, so the API layer decides how a
rejection is reported (per-file result).
"""

import html
import io
import re
import unicodedata
import zipfile
from pathlib import PurePosixPath, PureWindowsPath
from typing import Protocol

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
    ".xlsx": {XLSX},
}
ALLOWED_EXTENSIONS = sorted(EXT_TO_MIMES)
ALLOWED_MIME_TYPES = sorted({m for v in EXT_TO_MIMES.values() for m in v})

MAX_ZIP_MEMBERS = 1000
MAX_ZIP_UNCOMPRESSED = 200 * 1024 * 1024


class UploadRejected(Exception):
    """A file failed validation. `code` is one of the ADR-0002 per-file error codes."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code, self.detail = code, detail


class AsyncReader(Protocol):
    async def read(self, n: int, /) -> bytes: ...


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
    if b"\x00" not in head:
        try:
            head.decode("utf-8")
        except UnicodeDecodeError as e:
            # a multibyte char may be cut at the end of the sample
            if e.start < len(head) - 4:
                return None
        return "text/csv" if ext == ".csv" else "text/plain"
    return None


async def read_limited(file: AsyncReader, limit: int | None = None) -> bytes:
    """Read the upload in chunks, rejecting as soon as it exceeds `limit`."""
    limit = MAX_FILE_SIZE if limit is None else limit
    buf = bytearray()
    while chunk := await file.read(CHUNK_SIZE):
        buf.extend(chunk)
        if len(buf) > limit:
            raise UploadRejected("too_large", "File is too large")
    return bytes(buf)


def safe_filename(name: str) -> str:
    """Basename only (both separators), control chars dropped, bounded length."""
    base = PureWindowsPath(PurePosixPath(name).name).name
    base = "".join(ch for ch in base if ch.isprintable())[:255].strip()
    return base


def extension_of(filename: str) -> str:
    """Lower-cased allowlisted extension or UploadRejected(bad_extension)."""
    ext = PurePosixPath(safe_filename(filename)).suffix.lower()
    if ext not in EXT_TO_MIMES:
        raise UploadRejected("bad_extension", f"File extension not allowed: {ext or 'none'}")
    return ext


def validate_content(contents: bytes, ext: str) -> str:
    """Return the sniffed MIME or raise UploadRejected; extension must agree with content."""
    if not contents:
        raise UploadRejected("empty_file", "File is empty")
    mime_type = sniff_mime(contents, ext)
    if mime_type is None or mime_type not in ALLOWED_MIME_TYPES:
        raise UploadRejected("bad_content_type", "File content type not allowed")
    if mime_type not in EXT_TO_MIMES[ext]:
        raise UploadRejected("content_mismatch", "File extension does not match content")
    return mime_type


# ---- prompt-injection sanitising ------------------------------------------------------------
# One shared implementation for retrieved chunks, tool output, question text and rendered
# answers. Pipeline: (1) cut to max_chars, (2) html.unescape once (so the result is idempotent
# and entity-encoded tags cannot become live later), (3) strip control/format chars, (4) NFKC so
# look-alike brackets collapse, (5) defang role lines and exfil-capable links/images/URLs,
# (6) escape & < > exactly once, (7) cap again AFTER escaping (escaping can grow text 5x).
# Every regex quantifier is bounded so hostile input cannot cause quadratic backtracking.
_ESC = str.maketrans({"<": "&lt;", ">": "&gt;", "&": "&amp;"})
MAX_UNTRUSTED_CHARS = 20_000

# C0/C1 controls (not \n \t), zero-width, bidi and other format (Cf) characters
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_ROLE_LINE = re.compile(
    r"(?im)^([ \t>*#_-]{0,20})(system|assistant|user|human|developer|tool|ai)"
    r"([ \t]{0,50}(?:\[[^\]\n]{0,50}\])?[ \t]{0,50}):"
)
_IMAGE = re.compile(r"!\[[^\]\n]{0,150}\][ \t]{0,20}(?:\([^)\n]{0,2000}\)|\[[^\]\n]{0,200}\])")
_LINK = re.compile(r"\[([^\]\n]{0,150})\][ \t]{0,20}\([^)\n]{0,2000}\)")
_REF_DEF = re.compile(r"(?m)^[ \t]{0,20}\[[^\]\n]{1,200}\]:[ \t]{0,20}\S.{0,2000}$")
_URL = re.compile(
    r"(?i)\b(?:https?|ftp|ftps|file|data|javascript|vbscript|mailto)[ \t]{0,5}:(?://)?[^\s<>\"')\]]{0,2000}"
)
_AUTOLINK = re.compile(r"(?i)\bwww\.[^\s<>\"')\]]{0,2000}")
_PARTIAL_ENTITY = re.compile(r"&(?:a(?:mp?)?|lt?|gt?)?$")


def strip_invisible(text: str) -> str:
    """Drop control, zero-width, bidi and other invisible format characters (keeps \\n, \\t)."""
    text = _CTRL.sub("", text)
    return "".join(c for c in text if c in "\n\t" or unicodedata.category(c) not in ("Cf", "Cc", "Co", "Cs"))


def escape(text: str) -> str:
    """Neutralise & < > so data cannot forge or close a delimiter tag or inject HTML."""
    return text.translate(_ESC)


def defang(text: str) -> str:
    """Neutralise role markers and rendering/exfiltration vectors (markdown links, images, URLs).
    Links are ALWAYS removed, including inside code fences and tables."""
    text = _IMAGE.sub("[image removed]", text)
    text = _LINK.sub(lambda m: f"{m.group(1)} [link removed]" if m.group(1) else "[link removed]", text)
    text = _REF_DEF.sub("[link removed]", text)
    text = _URL.sub("[url removed]", text)
    text = _AUTOLINK.sub("[url removed]", text)
    text = text.replace("](", "] (").replace("![", "! [")  # whatever the bounded regexes skipped
    return _ROLE_LINE.sub(lambda m: f"{m.group(1)}{m.group(2)} (quoted text){m.group(3)}-", text)


def sanitize_untrusted(text: str, max_chars: int = MAX_UNTRUSTED_CHARS) -> str:
    """Full pipeline for retrieved chunks and tool output before they enter a prompt.
    Idempotent; the result is at most `max_chars` long (cut after escaping, never mid-entity)."""
    text = html.unescape(str(text)[:max_chars])
    text = unicodedata.normalize("NFKC", strip_invisible(text))
    out = escape(defang(text))
    if len(out) > max_chars:
        out = _PARTIAL_ENTITY.sub("", out[:max_chars])
    return out


def sanitize_output(text: str, max_chars: int = MAX_UNTRUSTED_CHARS) -> str:
    """Model text / citation spans leaving the API: same pipeline, so a client that renders
    markdown or HTML can never fetch an attacker URL. Escaped exactly once (idempotent), so
    "R&D" is returned as "R&amp;D" whether or not the model echoed an already-escaped excerpt."""
    return sanitize_untrusted(text, max_chars)
