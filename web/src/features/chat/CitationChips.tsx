import type { Citation } from '../../api/contract'
import { citationLabel } from './label'

export default function CitationChips({
  citations,
  activeChunkId,
  onOpen,
}: {
  citations: Citation[]
  activeChunkId?: string
  onOpen: (c: Citation) => void
}) {
  if (citations.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1.5">
      {citations.map((c) => (
        <button
          key={`${c.doc_id}:${c.chunk_id}`}
          type="button"
          aria-pressed={activeChunkId === c.chunk_id}
          onClick={() => onOpen(c)}
          className="rounded-full border border-transparent bg-link-soft px-2.5 py-[3px] text-xs font-semibold text-link hover:border-link aria-pressed:bg-link aria-pressed:text-panel"
        >
          {citationLabel(c)}
        </button>
      ))}
    </div>
  )
}
