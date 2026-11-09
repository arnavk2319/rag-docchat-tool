const ENTITIES: Record<string, string> = {
  '&lt;': '<',
  '&gt;': '>',
  '&amp;': '&',
  '&quot;': '"',
  '&#39;': "'",
  '&#x27;': "'",
}

/**
 * The backend HTML-escapes `< > &` in stream text (api.md). Undo exactly those entities in one
 * pass (so `&amp;lt;` becomes `&lt;`). Output is only ever rendered as a React text node.
 */
export function decodeEntities(s: string): string {
  return s.replace(/&(?:lt|gt|amp|quot|#39|#x27);/g, (m) => ENTITIES[m] ?? m)
}
