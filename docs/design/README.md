# DocChat web design (approved 2026-11-08)

Source of truth: `docs/design/prototype.html` (open in a browser; single file, light theme only). Read its `<style>` tokens and layout; do not redesign. Mix of "Soft Product" (teal, bubbles, pills) and "Mail" (three panes, source passage).

## Layout
Desktop: `300px | 1fr | 340px` = left (Documents with checkboxes, Conversations) | Chat | Source passage. Max width 1320. At <=900px one pane at a time with Documents / Chat / Source tabs.

## Behaviour (acceptance-relevant)
- Ticking documents sets the chat scope; the client sends `doc_ids` with every `POST /v1/query`. Only `ready` documents are tickable; `processing` shows an "Indexing" pill, disabled.
- Each conversation keeps its own scope (client-side, `sessionStorage` map keyed by session_id; the API does not store scope). New chat starts with the current ticks. Switching restores that conversation's ticks. Changing ticks in a non-empty conversation appends a "Scope changed: now N documents" note.
- No documents ticked: composer disabled with a hint.
- Answer: outlined bubble; collapsible agent trace (open while streaming); citation chips (blue, pill) -> open source pane; stop reason line in mono.
- Source pane: filename, `page N · characters a-b`, passage with the cited span in `<mark>`. Text only, never HTML.
- Footer note in left pane: "Demo data resets in 24 hours."

## Tokens (light only; no dark mode in v1)
bg `#eceeec`, panel `#fff`, side `#f3f4f2`, line `#dcdedb`, text `#1f2523`, muted `#5f6764`, primary teal `#0f766e` (actions, own messages, selection), primary-soft `#e3f1ee`, primary-text `#0b5d57`, link blue `#2f6fe4` (citations/links only), link-soft `#e6eefc`, mark `#ffe9a8`, warn `#8a5a00` on `#fdf0d8`. Radii 6/10/16. Fonts: DM Sans (UI), JetBrains Mono (refs, trace), self-hosted (see ADR-0004 amendment). Visible focus ring `2px var(--link)`.
