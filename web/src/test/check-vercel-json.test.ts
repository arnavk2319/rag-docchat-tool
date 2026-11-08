import { describe, expect, it } from 'vitest'
import { checkConfig } from '../../scripts/check-vercel-json.mjs'
import { buildConfig, PLACEHOLDER_ORIGIN } from '../../scripts/gen-vercel-json.mjs'

describe('check-vercel-json', () => {
  it('rejects the placeholder origin', () => {
    expect(() => checkConfig(buildConfig(PLACEHOLDER_ORIGIN))).toThrow(/placeholder/)
  })
  it('accepts a real origin and returns it', () => {
    expect(checkConfig(buildConfig('https://api.staging.docchat.dev'))).toBe(
      'https://api.staging.docchat.dev',
    )
    expect(checkConfig(buildConfig('https://api.staging.docchat.dev', { mock: true }))).toBe(
      'https://api.staging.docchat.dev',
    )
  })
  it('rejects missing CSP, missing connect-src and malformed origins', () => {
    expect(() => checkConfig({})).toThrow(/Content-Security-Policy/)
    const noConnect = {
      headers: [{ headers: [{ key: 'Content-Security-Policy', value: "default-src 'none'" }] }],
    }
    expect(() => checkConfig(noConnect)).toThrow(/connect-src/)
    const wild = {
      headers: [
        {
          headers: [
            { key: 'Content-Security-Policy', value: "connect-src 'self' https://*.x.com" },
          ],
        },
      ],
    }
    expect(() => checkConfig(wild)).toThrow()
  })
})
