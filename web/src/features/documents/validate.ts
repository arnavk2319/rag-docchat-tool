import { messageFor } from '../../api/errors'

export const MAX_FILES = 10
export const MAX_FILE_BYTES = 10 * 1024 * 1024
export const ALLOWED_EXT = ['pdf', 'docx', 'txt', 'csv', 'xlsx'] as const
export const ACCEPT = ALLOWED_EXT.map((e) => `.${e}`).join(',')

export interface FileProblem {
  filename: string
  message: string
}

const extOf = (name: string): string => {
  const i = name.lastIndexOf('.')
  return i < 0 ? '' : name.slice(i + 1).toLowerCase()
}

/** Splits a selection into files worth sending and per-file problems (client-side pre-checks). */
export function precheck(files: File[]): { ok: File[]; problems: FileProblem[] } {
  const ok: File[] = []
  const problems: FileProblem[] = []
  for (const f of files) {
    if (ok.length >= MAX_FILES) {
      problems.push({ filename: f.name, message: messageFor('too_many_files') })
    } else if (!(ALLOWED_EXT as readonly string[]).includes(extOf(f.name))) {
      problems.push({ filename: f.name, message: messageFor('bad_extension') })
    } else if (f.size === 0) {
      problems.push({ filename: f.name, message: messageFor('empty_file') })
    } else if (f.size > MAX_FILE_BYTES) {
      problems.push({ filename: f.name, message: messageFor('too_large') })
    } else {
      ok.push(f)
    }
  }
  return { ok, problems }
}

export function describeDoc(
  filename: string,
  size: number,
  units: number | null | undefined,
): string {
  const ext = extOf(filename).toUpperCase() || 'FILE'
  const kb =
    size < 1024 * 1024
      ? `${Math.max(1, Math.round(size / 1024))} KB`
      : `${(size / 1048576).toFixed(1)} MB`
  const count =
    units && ext === 'PDF'
      ? `${units} ${units === 1 ? 'page' : 'pages'}`
      : units && ext === 'XLSX'
        ? `${units} ${units === 1 ? 'sheet' : 'sheets'}`
        : null
  return [ext, count, kb].filter(Boolean).join(' · ')
}
