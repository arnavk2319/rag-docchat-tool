"""Minimal document parsers returning page/sheet-tagged plain text. No OCR.

Importing this module has no side effects. Failures raise ParseError.
"""

import csv
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLS = "application/vnd.ms-excel"
CSV = "text/csv"
TXT = "text/plain"

# Real decompressed-byte cap for zip containers (declared sizes can be forged).
MAX_DECOMPRESSED_BYTES = 200 * 1024 * 1024

log = logging.getLogger(__name__)


class ParseError(Exception):
    """The file could not be parsed."""


class DecompressionLimitError(ParseError):
    """A zip container expands past MAX_DECOMPRESSED_BYTES."""


@dataclass(frozen=True)
class ParsedPage:
    text: str
    source: str  # "page 3", "sheet Sales", "section 1", ...
    page: int | None = None
    sheet: str | None = None


def check_zip_bomb(path: str, limit: int | None = None) -> None:
    """Stream-decompress every member counting actual bytes; abort past the cap."""
    limit = MAX_DECOMPRESSED_BYTES if limit is None else limit
    total = 0
    try:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                with z.open(info) as f:
                    while chunk := f.read(64 * 1024):
                        total += len(chunk)
                        if total > limit:
                            raise DecompressionLimitError(
                                "archive expands beyond the decompression limit"
                            )
    except (zipfile.BadZipFile, NotImplementedError, OSError, RuntimeError, ValueError) as e:
        raise ParseError(f"invalid zip container: {e}") from e


def load_pdf(path: str) -> list[ParsedPage]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(path)
        return [
            ParsedPage(text=p.extract_text() or "", source=f"page {i}", page=i)
            for i, p in enumerate(reader.pages, start=1)
        ]
    except Exception as e:
        raise ParseError(f"cannot read PDF: {e}") from e


def load_txt(path: str) -> list[ParsedPage]:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise ParseError(f"cannot read text file: {e}") from e
    return [ParsedPage(text=text, source="section 1")]


def load_docx(path: str) -> list[ParsedPage]:
    check_zip_bomb(path)
    try:
        import docx

        d = docx.Document(path)
        parts = [p.text for p in d.paragraphs if p.text]
        for t in d.tables:
            parts += [" | ".join(c.text for c in row.cells) for row in t.rows]
    except Exception as e:
        raise ParseError(f"cannot read DOCX: {e}") from e
    return [ParsedPage(text="\n".join(parts), source="section 1")]


def load_csv(path: str) -> list[ParsedPage]:
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = [", ".join(r) for r in csv.reader(f)]
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        raise ParseError(f"cannot read CSV: {e}") from e
    return [ParsedPage(text="\n".join(rows), source="sheet 1", sheet="1")]


def load_xlsx(path: str) -> list[ParsedPage]:
    check_zip_bomb(path)
    try:
        import pandas as pd

        sheets = pd.read_excel(path, sheet_name=None, header=None, engine="openpyxl")
    except Exception as e:
        raise ParseError(f"cannot read Excel file: {e}") from e
    out = []
    for name, df in sheets.items():
        rows = [
            ", ".join("" if pd.isna(v) else str(v) for v in row)
            for row in df.itertuples(index=False)
        ]
        out.append(ParsedPage(text="\n".join(rows), source=f"sheet {name}", sheet=str(name)))
    return out


def load_xls(path: str) -> list[ParsedPage]:
    """Legacy .xls is unsupported: xlrd is not installed, so this always raises ParseError."""
    try:
        import pandas as pd

        sheets = pd.read_excel(path, sheet_name=None, header=None)
    except Exception as e:
        raise ParseError(f"cannot read legacy Excel file: {e}") from e
    return [
        ParsedPage(
            text="\n".join(", ".join(str(v) for v in r) for r in df.itertuples(index=False)),
            source=f"sheet {n}",
            sheet=str(n),
        )
        for n, df in sheets.items()
    ]


_LOADERS = {
    PDF: load_pdf,
    TXT: load_txt,
    DOCX: load_docx,
    CSV: load_csv,
    XLSX: load_xlsx,
    XLS: load_xls,
}


def parse_uploaded_file(file_path: str, mime_type: str) -> list[ParsedPage]:
    loader = _LOADERS.get(mime_type)
    if loader is None:
        raise ParseError(f"unsupported type: {mime_type}")
    log.info("parsing upload as %s", mime_type)
    return loader(file_path)
