import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { DocumentsPanel } from '../features/documents/DocumentsPanel'

/** Standalone /documents page: local selection state; the app shell (T-041) owns real scope. */
export default function Documents() {
  const [client] = useState(() => new QueryClient())
  const [selected, setSelected] = useState<string[]>([])
  return (
    <QueryClientProvider client={client}>
      <div className="max-w-[300px] border border-line bg-side">
        <DocumentsPanel
          selected={selected}
          onToggle={(id) =>
            setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))
          }
          onSelectAll={setSelected}
          onClear={() => setSelected([])}
        />
      </div>
    </QueryClientProvider>
  )
}
