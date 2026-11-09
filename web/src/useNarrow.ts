import { useSyncExternalStore } from 'react'

const NARROW = '(max-width: 900px)'

function subscribe(cb: () => void): () => void {
  if (typeof window.matchMedia !== 'function') return () => undefined
  const mq = window.matchMedia(NARROW)
  mq.addEventListener('change', cb)
  return () => mq.removeEventListener('change', cb)
}
const snapshot = () =>
  typeof window.matchMedia === 'function' ? window.matchMedia(NARROW).matches : false

/** True at <=900px, where the three panes become tabs. */
export function useNarrow(): boolean {
  return useSyncExternalStore(subscribe, snapshot, () => false)
}
