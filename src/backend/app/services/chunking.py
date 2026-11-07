"""Parent-child chunking (pure functions, no I/O).

Each ParsedPage is split into parents (~1200 tokens) and each parent into children (~300
tokens), so no chunk ever crosses a page or sheet boundary. No tokenizer is available, so
tokens are approximated as len(text) / 4 characters (CHARS_PER_TOKEN); targets are constants.

Targets are upper bounds: a chunk is at most *_TARGET_CHARS long and, except for the last
chunk of its parent/page, at least MIN_FRACTION of the target (tolerance), unless a
hard cut is needed. Breaks prefer paragraph, then sentence, then whitespace, then a hard cut.
Offsets are relative to the page text: page.text[char_start:char_end] == chunk text.
Chunks are trimmed of surrounding whitespace; whitespace-only chunks are dropped.

Sections: simple heading heuristics (markdown '#', numbered '1.2 Title', short ALL-CAPS lines).
A chunk's section is the nearest heading at or before its start, carried across pages of the
document; '' when none (never None, Chroma metadata must not contain None). Skipped for
sheets (csv/xlsx) where rows are data, not prose.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field

from app.utilities.file_handler import ParsedPage

CHARS_PER_TOKEN = 4
CHILD_TOKENS = 300
PARENT_TOKENS = 1200
CHILD_TARGET_CHARS = CHILD_TOKENS * CHARS_PER_TOKEN  # 1200
PARENT_TARGET_CHARS = PARENT_TOKENS * CHARS_PER_TOKEN  # 4800
MIN_FRACTION = 0.5  # a break is searched for only in the last half of the window
MAX_SECTION_CHARS = 120

_SENTENCE_END = re.compile(r"[.!?][\"')\]]*\s")
_MD_HEADING = re.compile(r"^#{1,6}\s+(\S.*)$")
_NUM_HEADING = re.compile(
    r"^(?:\d{1,2}(?:\.\d{1,2})+\.?[)]?|\d{1,2}[.)]?|Section\s+\d{1,2}[.)]?)\s+[A-Z]\S*(?:\s.*)?$"
)
MAX_HEADING_CHARS = 80
MAX_HEADING_WORDS = 12


@dataclass
class ChildChunk:
    chunk_id: str
    parent_id: str
    text: str
    char_start: int
    char_end: int
    section: str


@dataclass
class ParentChunk:
    parent_id: str
    text: str
    char_start: int
    char_end: int
    section: str
    source: str
    page: int | None
    sheet: str | None
    children: list[ChildChunk] = field(default_factory=list)


def _find_break(text: str, pos: int, limit: int, floor: int) -> int:
    """Best cut index in [floor, limit] (text[:cut] ends a unit); limit if none found."""
    region = text[floor:limit]
    i = region.rfind("\n\n")
    if i >= 0:
        return floor + i + 2
    last = None
    for m in _SENTENCE_END.finditer(region):
        last = m
    if last is not None:
        return floor + last.end()
    for sep in ("\n", " ", "\t"):
        i = region.rfind(sep)
        if i >= 0:
            return floor + i + 1
    return limit


def split_span(text: str, start: int, end: int, target: int) -> list[tuple[int, int]]:
    """Split text[start:end] into trimmed, non-empty (s, e) spans each <= target chars."""
    spans: list[tuple[int, int]] = []
    pos = start
    while pos < end:
        limit = min(pos + target, end)
        cut = limit if limit == end else _find_break(text, pos, limit, pos + int(target * MIN_FRACTION))
        s, e = pos, cut
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e > s:
            spans.append((s, e))
        pos = cut
    return spans


def is_heading(line: str) -> str | None:
    """Return the heading text if the line looks like a heading, else None."""
    s = line.strip()
    if not s or len(s) > MAX_HEADING_CHARS or len(s.split()) > MAX_HEADING_WORDS:
        return None
    m = _MD_HEADING.match(s)
    if m:
        return m.group(1).strip("# ").strip() or None
    if _NUM_HEADING.match(s) and not s.endswith((".", "?", "!", ",", ";")):
        return s
    letters = [c for c in s if c.isalpha()]
    if len(letters) >= 3 and len(s) <= 80 and s.upper() == s and not s.endswith((".", "?", "!", ",", ";")):
        return s
    return None


def find_headings(text: str) -> list[tuple[int, str]]:
    """(offset of line start, heading text) for every heading line, in order."""
    out: list[tuple[int, str]] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        h = is_heading(line)
        if h:
            out.append((pos, h))
        pos += len(line)
    return out


def _section_at(offsets: list[int], names: list[str], offset: int, carry: str) -> str:
    """Nearest heading at or before offset (bisect on pre-built sorted offsets)."""
    i = bisect_right(offsets, offset)
    return names[i - 1] if i else carry


def chunk_document(pages: list[ParsedPage], doc_id: str) -> list[ParentChunk]:
    """Split pages into parents with children; ids are '<doc_id>:<p>' and '<doc_id>:<p>:<c>'."""
    parents: list[ParentChunk] = []
    carry = ""
    for pg in pages:
        text = pg.text
        headings = find_headings(text) if pg.sheet is None else []
        offsets = [o for o, _ in headings]
        names = [h for _, h in headings]
        for ps, pe in split_span(text, 0, len(text), PARENT_TARGET_CHARS):
            pid = f"{doc_id}:{len(parents)}"
            parent = ParentChunk(
                pid, text[ps:pe], ps, pe, _section_at(offsets, names, ps, carry), pg.source, pg.page, pg.sheet
            )
            for cs, ce in split_span(text, ps, pe, CHILD_TARGET_CHARS):
                parent.children.append(
                    ChildChunk(
                        f"{pid}:{len(parent.children)}",
                        pid,
                        text[cs:ce],
                        cs,
                        ce,
                        _section_at(offsets, names, cs, carry),
                    )
                )
            parents.append(parent)
        if headings:
            carry = headings[-1][1]
    return parents
