import { describe, expect, it } from 'vitest'
import { initialState, reducer } from './reducer'
import type { AssistantMsg } from './reducer'

const tok = (text: string) => ({ type: 'event', event: { event: 'token', text } }) as never

describe('reducer token entity handling', () => {
  it('does not show a half-received entity and decodes it once complete', () => {
    let s = reducer(initialState, { type: 'send', question: 'q' })
    s = reducer(s, tok('a &am'))
    const mid = s.messages[1] as AssistantMsg
    expect(mid.answer).toBe('a ')
    s = reducer(s, tok('p; b'))
    expect((s.messages[1] as AssistantMsg).answer).toBe('a & b')
  })
})
