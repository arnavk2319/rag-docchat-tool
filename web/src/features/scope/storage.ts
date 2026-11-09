/** Per-conversation scope (design README): sessionStorage map keyed by session_id. */
const KEY = 'docchat.scope'

function readAll(): Record<string, string[]> {
  try {
    const v: unknown = JSON.parse(sessionStorage.getItem(KEY) ?? '{}')
    return typeof v === 'object' && v !== null && !Array.isArray(v)
      ? (v as Record<string, string[]>)
      : {}
  } catch {
    return {}
  }
}

export function loadScope(sessionId: string): string[] | null {
  const v = readAll()[sessionId]
  return Array.isArray(v) && v.every((x) => typeof x === 'string') ? v : null
}

export function saveScope(sessionId: string, ids: string[]): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ ...readAll(), [sessionId]: ids }))
  } catch {
    // storage full or unavailable: scope just isn't remembered
  }
}
