import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Shell from './Shell'

/** The one app-wide QueryClientProvider (documents list/upload/remove; chat state is not cached). */
export default function AppProviders() {
  const [client] = useState(() => new QueryClient())
  return (
    <QueryClientProvider client={client}>
      <main className="p-4">
        <Shell />
      </main>
    </QueryClientProvider>
  )
}
