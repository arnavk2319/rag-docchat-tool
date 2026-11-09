import type { components } from '../../api/schema'

type S = components['schemas']
export type DocSummary = S['DocSummaryResponse']
export type DocSentiment = S['DocSentimentResponse']
export type CompareResponse = S['CompareResponse']
export type ComparePoint = S['ComparePoint']
