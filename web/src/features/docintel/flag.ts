/** Doc-intel (summary / sentiment / compare) is built in only when VITE_FEATURE_DOC_INTEL=1. */
export const docIntelEnabled = (): boolean => import.meta.env.VITE_FEATURE_DOC_INTEL === '1'

export type IntelKind = 'summary' | 'sentiment' | 'compare'
export const INTEL_KINDS: { kind: IntelKind; label: string }[] = [
  { kind: 'summary', label: 'Summary' },
  { kind: 'sentiment', label: 'Sentiment' },
  { kind: 'compare', label: 'Compare' },
]
