# Chunking

`services/chunking.py` (pure, no I/O). Parent-child per ADR-0001 decision 8.

- Each `ParsedPage` is split into parents (~1200 tokens), each parent into children (~300 tokens). Splitting happens per page/sheet, so no chunk crosses a boundary.
- No tokenizer: tokens ~ chars/4. Constants: `CHILD_TARGET_CHARS=1200`, `PARENT_TARGET_CHARS=4800`; chunks are at most the target, and breaks are searched only in the last half of the window (`MIN_FRACTION`).
- Break preference: paragraph (`\n\n`), sentence end, whitespace, hard cut. No overlap. Chunks are whitespace-trimmed; empty ones dropped.
- Ids: parent `<doc_id>:<p>`, child `<doc_id>:<p>:<c>`. Offsets `char_start`/`char_end` are relative to the page/sheet text: `page.text[s:e] == chunk.text`.
- Section: heading heuristics (markdown `#`; numbered headings need a 1-2 digit (optionally dotted) number such as `3`, `1.`, `1.2`, `3.4.1`, `2)` or `Section 2` followed by a capitalised title, so a year or lowercase-prose line starts no heading; short ALL-CAPS line). Candidates are <= 80 chars, <= 12 words and do not end in `. ? ! , ;`; nearest heading at or before the chunk start, carried across pages; `''` if unknown; skipped for sheets.
- Storage: children embedded in Chroma (metadata incl. `parent_id`, `section`, offsets, page|sheet). Parents in SQLite `parents` (`store/parents.py`), owner-scoped. `services/parent_lookup.expand_to_parents` maps child hits to deduplicated parents.
