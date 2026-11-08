/** Error mapping (ADR-0004). Messages are plain text; raw server `detail` is never shown. */

export const GENERIC_MESSAGE = 'Something went wrong. Please try again.'

const MESSAGES: Record<string, string> = {
  rate_limited: 'Too many requests. Please wait a moment and try again.',
  spend_cap_exceeded: 'The daily demo spend limit has been reached. Please try again tomorrow.',
  budget_exceeded: 'This request exceeded its budget. Try a shorter question.',
  llm_unavailable: 'The language model is unavailable right now. Please try again shortly.',
  llm_bad_output: 'The model returned an unusable answer. Please try again.',
  document_busy: 'This document is still being processed. Try again in a moment.',
  unauthorized: 'Authentication is required.',
  global_rate_limited: 'The service is busy right now. Please try again shortly.',
  global_spend_cap_exceeded:
    'The demo has reached its daily spend limit. Please try again tomorrow.',
  tenant_spend_cap_exceeded:
    'Your organization reached its daily spend limit. Please try again tomorrow.',
  session_limit_exceeded: 'You have too many conversations. Delete one to start a new one.',
  bad_request: 'The request was not valid.',
  conflict: 'That action clashes with the current state. Please try again.',
  method_not_allowed: 'That action is not supported.',
  auth_required: 'Authentication is required.',
  auth_failed: 'Your session could not be renewed. Reload the page to start a new one.',
  token_expired: 'Your session expired.',
  invalid_token: 'Your session is invalid.',
  mint_rate_limited: 'Too many visitors right now. Please try again later.',
  not_ready: 'This document is not ready yet.',
  not_found: 'Not found.',
  validation_error: 'The request was not valid.',
  internal: GENERIC_MESSAGE,
  network_error: 'Could not reach the server. Check your connection.',
  // request-level upload faults
  no_files: 'Choose at least one file to upload.',
  too_many_files: 'Too many files. Upload at most 10 at a time.',
  // per-file upload codes
  bad_extension: 'Unsupported file type. Use PDF, DOCX, TXT, CSV or XLSX.',
  empty_file: 'The file is empty.',
  too_large: 'The file is too large (10 MB per file, 50 MB per upload).',
  content_mismatch: 'The file content does not match its extension.',
  bad_content_type: 'The file type is not allowed.',
  parse_error: 'The file could not be read.',
  empty_document: 'No text could be extracted from the file.',
  ingest_failed: 'Processing the file failed. Please try again.',
}

export function messageFor(code: string | null | undefined): string {
  if (code && Object.hasOwn(MESSAGES, code)) return MESSAGES[code]
  return GENERIC_MESSAGE
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string) {
    super(messageFor(code))
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

/** Extracts `code` from an `{detail, code}` body; anything else yields null. */
export function codeFromBody(body: unknown): string | null {
  if (typeof body === 'object' && body !== null && 'code' in body) {
    const c = (body as { code: unknown }).code
    if (typeof c === 'string') return c
  }
  return null
}

export function apiErrorFrom(status: number, body: unknown): ApiError {
  return new ApiError(status, codeFromBody(body) ?? 'internal')
}
