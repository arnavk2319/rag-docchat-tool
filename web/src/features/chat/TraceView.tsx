import type { TraceItem } from './reducer'

/** Collapsible agent timeline; open while streaming. Text nodes only. */
export default function TraceView({
  trace,
  streaming,
}: {
  trace: TraceItem[]
  streaming: boolean
}) {
  if (trace.length === 0 && !streaming) return null
  const steps = trace.filter((t) => t.kind === 'step').length
  return (
    <details className="text-[12.5px] text-muted" open={streaming} data-testid="trace">
      <summary className="cursor-pointer rounded-sm py-0.5">
        {streaming ? 'Working…' : `Agent trace · ${steps} ${steps === 1 ? 'step' : 'steps'}`}
      </summary>
      <ol className="m-0 mt-1.5 flex list-none flex-col gap-0.5 border-l-2 border-line p-0">
        {trace.map((t, i) => (
          <li key={i} className="pl-2.5 font-mono text-[11.5px]" data-kind={t.kind}>
            {t.text}
          </li>
        ))}
      </ol>
    </details>
  )
}
