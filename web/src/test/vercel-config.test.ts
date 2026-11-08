import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, readdirSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  buildConfig,
  buildCsp,
  parseApiOrigin,
  PLACEHOLDER_ORIGIN,
} from '../../scripts/gen-vercel-json.mjs'

const root = process.cwd()
const committed = JSON.parse(readFileSync(join(root, 'vercel.json'), 'utf8')) as ReturnType<
  typeof buildConfig
>
const headerOf = (cfg: typeof committed, source: string, key: string) =>
  cfg.headers.find((h) => h.source === source)?.headers.find((h) => h.key === key)?.value

const EXPECTED_CSP =
  "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; " +
  "connect-src 'self' https://api.docchat.example; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"

describe('committed vercel.json', () => {
  it('has the SPA rewrite', () => {
    expect(committed.rewrites).toEqual([{ source: '/(.*)', destination: '/index.html' }])
  })

  it('has the exact CSP and security headers', () => {
    expect(headerOf(committed, '/(.*)', 'Content-Security-Policy')).toBe(EXPECTED_CSP)
    expect(headerOf(committed, '/(.*)', 'X-Content-Type-Options')).toBe('nosniff')
    expect(headerOf(committed, '/(.*)', 'Referrer-Policy')).toBe('no-referrer')
    expect(headerOf(committed, '/(.*)', 'Permissions-Policy')).toBe('')
  })

  it('has no wildcard in connect-src and no unsafe-* anywhere', () => {
    const csp = headerOf(committed, '/(.*)', 'Content-Security-Policy')!
    const connect = csp.split(';').find((d) => d.trim().startsWith('connect-src'))!
    expect(connect).not.toContain('*')
    expect(csp).not.toMatch(/unsafe-inline|unsafe-eval/)
    expect(csp).not.toContain('worker-src')
  })

  it('caches assets immutably', () => {
    expect(headerOf(committed, '/assets/(.*)', 'Cache-Control')).toBe(
      'public, max-age=31536000, immutable',
    )
  })

  it('is in sync with the generator (placeholder origin)', () => {
    expect(committed).toEqual(buildConfig(PLACEHOLDER_ORIGIN))
  })
})

describe('API origin templating (scripts/gen-vercel-json.mjs)', () => {
  it('puts the given origin in connect-src', () => {
    expect(buildCsp('https://api.example.com')).toContain(
      "connect-src 'self' https://api.example.com;",
    )
  })

  it('mock/demo build adds only worker-src', () => {
    const plain = buildCsp('https://api.example.com')
    expect(buildCsp('https://api.example.com', { mock: true })).toBe(`${plain}; worker-src 'self'`)
  })

  it.each([
    '*',
    'https://*.example.com',
    'http://api.example.com',
    'https://api.example.com/v1',
    'https://user:pw@api.example.com',
    'https://api.example.com?x=1',
    'https://a.com;script-src',
    'https://a.com,b.com',
    "https://a.com'x",
    'https://a.com x',
    'https://a.com\nb',
    'https://a.com\\b',
    'https://a.com\u0000',
    'not a url',
    '',
  ])('rejects %j', (bad) => {
    expect(() => parseApiOrigin(bad)).toThrow()
  })

  it('normalises uppercase hostnames', () => {
    expect(parseApiOrigin('https://API.Example.COM')).toBe('https://api.example.com')
  })

  it('accepts origins with port, trailing slash and localhost http', () => {
    expect(parseApiOrigin('https://api.example.com:8443/')).toBe('https://api.example.com:8443')
    expect(parseApiOrigin('http://localhost:8000')).toBe('http://localhost:8000')
  })

  it('CLI writes a file from API_ORIGIN', () => {
    const out = join(mkdtempSync(join(tmpdir(), 'vj-')), 'vercel.json')
    execFileSync('node', [join(root, 'scripts/gen-vercel-json.mjs'), '--mock', '--out', out], {
      env: { ...process.env, API_ORIGIN: 'https://api.example.org' },
    })
    const cfg = JSON.parse(readFileSync(out, 'utf8')) as typeof committed
    const csp = headerOf(cfg, '/(.*)', 'Content-Security-Policy')
    expect(csp).toContain('https://api.example.org')
    expect(csp).toMatch(/worker-src 'self'$/)
  })

  it('CLI fails on a bad origin', () => {
    expect(() =>
      execFileSync('node', [join(root, 'scripts/gen-vercel-json.mjs'), '--out', '/dev/null'], {
        env: { ...process.env, API_ORIGIN: 'https://*.evil.com' },
        stdio: 'pipe',
      }),
    ).toThrow()
  })
})

describe('index.html has no inline script or style', () => {
  const inline = /<script(?![^>]*\bsrc\b)|<style|\sstyle=|\son[a-z]+=/i
  it('source index.html', () => {
    expect(readFileSync(join(root, 'index.html'), 'utf8')).not.toMatch(inline)
  })
  const dist = join(root, 'dist/index.html')
  it.runIf(existsSync(dist))('built dist/index.html (run npm run build first)', () => {
    expect(readFileSync(dist, 'utf8')).not.toMatch(inline)
  })
})

describe('no inline style attributes in src (CSP style-src self)', () => {
  const walk = (d: string): string[] =>
    readdirSync(d, { withFileTypes: true }).flatMap((e) =>
      e.isDirectory() ? walk(join(d, e.name)) : /\.(ts|tsx)$/.test(e.name) ? [join(d, e.name)] : [],
    )
  it('has no style= props, .style writes or setAttribute("style") in non-test source', () => {
    const bad = walk(join(root, 'src'))
      .filter((f) => !/\.test\.tsx?$/.test(f) && !f.includes('/src/test/'))
      .filter((f) => /\bstyle=|\.style\b|setAttribute\(\s*['"]style/.test(readFileSync(f, 'utf8')))
    expect(bad).toEqual([])
  })
})

describe('no secrets in built bundle', () => {
  const assets = join(root, 'dist/assets')
  it.runIf(existsSync(assets))('dist/assets has no key-shaped strings', () => {
    // The ticket's loose `sk-` grep matches React's "mask-type"; anchor to real key shapes.
    const re = /(^|[^a-z])sk-[A-Za-z0-9_-]{16,}|api[_-]?key|secret|bearer\s+[A-Za-z0-9._-]{20,}/i
    const bad = readdirSync(assets)
      .filter((f) => /\.(js|css)$/.test(f))
      .filter((f) => re.test(readFileSync(join(assets, f), 'utf8')))
    expect(bad).toEqual([])
  })
})

describe('built CSS has no data: font urls (font-src self)', () => {
  const assets = join(root, 'dist/assets')
  it.runIf(existsSync(assets))('no url(data:) in dist css', () => {
    const bad = readdirSync(assets)
      .filter((f) => f.endsWith('.css'))
      .filter((f) => /url\(\s*["']?data:/i.test(readFileSync(join(assets, f), 'utf8')))
    expect(bad).toEqual([])
  })
})
