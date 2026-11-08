/** Named re-exports from the generated OpenAPI types plus the SSE union (not in OpenAPI). */
import type { components } from './schema'

type S = components['schemas']

export type AnonymousToken = S['AnonymousToken']
export type ErrorBody = S['ErrorBody']
export type Citation = S['Citation']
export type Span = S['Span']
export type ChunkPassage = S['ChunkPassage']
export type Highlight = S['Highlight']
export type DocumentOut = S['DocumentOut']
export type DocumentList = S['DocumentList']
export type UploadResponse = S['UploadResponse']
export type UploadResult = S['UploadResult']
export type UploadError = S['UploadError']
export type QueryRequest = S['QueryRequest']
export type QueryResponse = S['QueryResponse']
export type SessionList = S['SessionList']
export type SessionListItem = S['SessionListItem']
export type SessionOut = S['SessionOut']
export type SessionMessageOut = S['SessionMessageOut']
export type SessionCreated = S['SessionCreated']

// --- Agent SSE (docs/architecture/api.md "Agent SSE"); every payload has run_id and seq ---

export type StoppedReason = 'done' | 'max_steps' | 'max_tokens' | 'max_usd' | 'abstained' | 'error'
export type AgentNode = 'router' | 'plan' | 'tool' | 'grade' | 'rewrite' | 'answer' | 'groundedness'

interface Base {
  run_id: string
  seq: number
}

export type StreamEvent =
  | ({ event: 'step' } & Base & { node: AgentNode; n: number; summary: string })
  | ({ event: 'tool_call' } & Base & { tool: string; args: Record<string, unknown> })
  | ({ event: 'tool_result' } & Base & { tool: string; ok: boolean; n_items: number })
  | ({ event: 'token' } & Base & { text: string })
  | ({ event: 'citations' } & Base & { citations: Citation[] })
  | ({ event: 'final' } & Base & {
        answer: string
        citations: Citation[]
        abstained: boolean
        stopped_reason: StoppedReason
        usage: { steps: number; tokens: number; usd: number }
      })
  | ({ event: 'error' } & Base & { code: string; detail: string })

export type StreamEventName = StreamEvent['event']
