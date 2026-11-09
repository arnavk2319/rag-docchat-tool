import { readdirSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

const SRC = join(process.cwd(), 'src')

function files(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((d) => {
    const p = join(dir, d.name)
    if (d.isDirectory()) return files(p)
    return /\.(ts|tsx)$/.test(d.name) ? [p] : []
  })
}

const outside = files(SRC)
  .map((p) => relative(SRC, p))
  .filter((r) => r !== 'api/schema.d.ts' && !/^(api|auth|mocks|test)\//.test(r))

describe('source hygiene', () => {
  it('scans some feature files', () => {
    expect(outside).toContain('App.tsx')
  })

  it('feature code never calls fetch(', () => {
    const bad = outside.filter((r) => /fetch\(/.test(readFileSync(join(SRC, r), 'utf8')))
    expect(bad).toEqual([])
  })

  it('feature code never mentions the storage key, mint path or Authorization header', () => {
    const bad = outside.filter((r) =>
      /docchat\.token|auth\/anonymous|Authorization/.test(readFileSync(join(SRC, r), 'utf8')),
    )
    expect(bad).toEqual([])
  })

  it('only auth/token.ts touches the storage key', () => {
    const holders = files(SRC)
      .map((p) => relative(SRC, p))
      .filter((r) => r !== 'api/schema.d.ts' && !r.endsWith('.test.ts') && !r.startsWith('mocks/'))
      .filter((r) => /docchat\.token/.test(readFileSync(join(SRC, r), 'utf8')))
    expect(holders).toEqual(['auth/token.ts'])
  })
})
