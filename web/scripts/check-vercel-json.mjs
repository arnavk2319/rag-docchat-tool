// Pre-deploy guard: fails if vercel.json still carries the placeholder API origin.
// Usage: node scripts/check-vercel-json.mjs [path] [--mock]   (npm run check:vercel)
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { PLACEHOLDER_ORIGIN, parseApiOrigin } from './gen-vercel-json.mjs'

/** Returns the connect-src API origin of a vercel.json object; throws if missing, invalid or placeholder. */
export function checkConfig(cfg) {
  const csp = cfg?.headers
    ?.flatMap((h) => h.headers ?? [])
    .find((h) => h.key === 'Content-Security-Policy')?.value
  if (typeof csp !== 'string') throw new Error('no Content-Security-Policy header found')
  const directive = csp
    .split(';')
    .map((s) => s.trim())
    .find((s) => s.startsWith('connect-src '))
  if (!directive) throw new Error('CSP has no connect-src directive')
  const origins = directive
    .split(/\s+/)
    .slice(1)
    .filter((t) => t !== "'self'")
  if (origins.length !== 1) throw new Error('connect-src must list exactly one API origin')
  const origin = parseApiOrigin(origins[0])
  if (origin === PLACEHOLDER_ORIGIN)
    throw new Error('connect-src still uses the placeholder origin; run gen:vercel with API_ORIGIN')
  return origin
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const file = process.argv.slice(2).find((a) => !a.startsWith('--')) ?? 'vercel.json'
  try {
    console.log(`ok: connect-src API origin ${checkConfig(JSON.parse(readFileSync(file, 'utf8')))}`)
  } catch (e) {
    console.error(`vercel.json check failed: ${e.message}`)
    process.exit(1)
  }
}
