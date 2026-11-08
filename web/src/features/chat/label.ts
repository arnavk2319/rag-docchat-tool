import type { Citation } from '../../api/contract'

export function citationLabel(c: Citation): string {
  if (c.page != null) return `${c.filename} · p.${c.page}`
  if (c.sheet) return `${c.filename} · ${c.sheet}`
  return c.filename
}
