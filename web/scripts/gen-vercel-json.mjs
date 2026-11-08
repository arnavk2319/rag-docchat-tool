// Generates web/vercel.json (ADR-0004 "CSP and vercel.json").
// Usage: API_ORIGIN=https://api.example.com node scripts/gen-vercel-json.mjs [--mock] [--out path]
// Hand edit is NOT the supported path: re-run this script and commit the result.
import { writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

export const PLACEHOLDER_ORIGIN = 'https://api.docchat.example'

/** Validates a bare https origin (no path, wildcard, credentials, query). Returns it normalised. */
export function parseApiOrigin(raw) {
  if (typeof raw !== 'string' || /[\s\x00-\x1f\x7f\\;,'"*]/.test(raw))
    throw new Error('API_ORIGIN contains forbidden characters')
  let u
  try {
    u = new URL(raw)
  } catch {
    throw new Error(`API_ORIGIN is not a URL: ${raw}`)
  }
  const local = u.hostname === 'localhost' || u.hostname === '127.0.0.1'
  if (u.protocol !== 'https:' && !(local && u.protocol === 'http:'))
    throw new Error('API_ORIGIN must be https (http only for localhost)')
  if (raw.includes('*')) throw new Error('API_ORIGIN must not contain a wildcard')
  if (
    u.username ||
    u.password ||
    u.search ||
    u.hash ||
    (u.pathname !== '/' && raw.replace(/\/+$/, '') !== u.origin)
  )
    throw new Error('API_ORIGIN must be an origin only (scheme://host[:port])')
  // Defence in depth: the WHATWG parser tolerates CSP delimiters in hosts; allow only DNS chars.
  if (!/^https?:\/\/[a-z0-9.-]+(:\d+)?$/.test(u.origin))
    throw new Error('API_ORIGIN host has invalid characters')
  return u.origin
}

export function buildCsp(apiOrigin, { mock = false } = {}) {
  const parts = [
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    `connect-src 'self' ${parseApiOrigin(apiOrigin)}`,
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
  ]
  if (mock) parts.push("worker-src 'self'")
  return parts.join('; ')
}

export function buildConfig(apiOrigin, opts = {}) {
  return {
    rewrites: [{ source: '/(.*)', destination: '/index.html' }],
    headers: [
      {
        source: '/(.*)',
        headers: [
          { key: 'Content-Security-Policy', value: buildCsp(apiOrigin, opts) },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'no-referrer' },
          { key: 'Permissions-Policy', value: '' },
        ],
      },
      {
        source: '/assets/(.*)',
        headers: [{ key: 'Cache-Control', value: 'public, max-age=31536000, immutable' }],
      },
    ],
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2)
  const out = args.includes('--out') ? args[args.indexOf('--out') + 1] : 'vercel.json'
  const cfg = buildConfig(process.env.API_ORIGIN ?? PLACEHOLDER_ORIGIN, {
    mock: args.includes('--mock'),
  })
  writeFileSync(out, JSON.stringify(cfg, null, 2) + '\n')
}
