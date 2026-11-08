import type { FileProblem } from './validate'

/** Per-file upload problems beside filenames. Everything is rendered as text. */
export function UploadErrors({ problems }: { problems: FileProblem[] }) {
  if (problems.length === 0) return null
  return (
    <ul
      aria-label="Upload problems"
      className="mx-2 my-1 flex list-none flex-col gap-1 rounded-md bg-warn-soft p-2 text-xs text-warn"
    >
      {problems.map((p, i) => (
        <li key={`${i}-${p.filename}`}>
          <span className="font-semibold break-all">{p.filename}</span>
          {': '}
          <span>{p.message}</span>
        </li>
      ))}
    </ul>
  )
}
