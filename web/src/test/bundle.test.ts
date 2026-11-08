import { readdirSync, readFileSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'
import { build } from 'vite'

// With VITE_MOCK unset the app must call only VITE_API_BASE_URL and ship no mock code.
const SRC = join(process.cwd(), 'src')

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((d) => {
    const p = join(dir, d.name)
    return d.isDirectory() ? walk(p) : [p]
  })
}

describe('no hard-coded API origin in source', () => {
  it('app source (non-test, non-mock, non-generated) has no http(s) URL literal', () => {
    const bad = walk(SRC)
      .filter((p) => /\.(ts|tsx)$/.test(p) && !/\.test\.tsx?$/.test(p))
      .map((p) => relative(SRC, p))
      .filter((r) => !/^(mocks|test)\//.test(r) && r !== 'api/schema.d.ts')
      .filter((r) => /https?:\/\/[a-z0-9]/i.test(readFileSync(join(SRC, r), 'utf8')))
    expect(bad).toEqual([])
  })

  it('the only fetch base is VITE_API_BASE_URL via api/config.ts', () => {
    const cfg = readFileSync(join(SRC, 'api/config.ts'), 'utf8')
    expect(cfg).toContain('VITE_API_BASE_URL')
  })
})

describe('production bundle (VITE_MOCK unset)', () => {
  it('contains no msw or mock fixtures', { timeout: 180_000 }, async () => {
    const out = mkdtempSync(join(tmpdir(), 'docchat-bundle-'))
    const prev = process.env.VITE_MOCK
    delete process.env.VITE_MOCK
    try {
      await build({
        root: process.cwd(),
        logLevel: 'silent',
        mode: 'production',
        publicDir: false,
        build: { outDir: out, emptyOutDir: true },
      })
      const js = walk(out)
        .filter((p) => p.endsWith('.js'))
        .map((p) => readFileSync(p, 'utf8'))
        .join('\n')
      expect(js.length > 1000).toBe(true)
      for (const needle of [
        'mockServiceWorker',
        'setupWorker',
        'mock-token-',
        'run-mock-1',
        'annual-report.pdf',
        '@mswjs',
      ]) {
        expect(js.includes(needle), needle).toBe(false)
      }
    } finally {
      if (prev !== undefined) process.env.VITE_MOCK = prev
      rmSync(out, { recursive: true, force: true })
    }
  })
})
