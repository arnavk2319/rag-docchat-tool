import { describe, expect, it } from 'vitest'
import { decodeEntities } from './text'

describe('decodeEntities', () => {
  it('decodes backend escapes to real characters, single pass', () => {
    expect(decodeEntities('a &lt;b&gt; &amp; c &quot;d&quot; &#39;e&#39;')).toBe(
      'a <b> & c "d" \'e\'',
    )
    expect(decodeEntities('&amp;lt;')).toBe('&lt;')
    expect(decodeEntities('&nbsp; &unknown;')).toBe('&nbsp; &unknown;')
  })
})
