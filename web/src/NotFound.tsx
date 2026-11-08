import { Link } from 'react-router-dom'

export default function NotFound() {
  return (
    <main className="p-4">
      <h1 className="text-lg font-semibold">Page not found</h1>
      <Link to="/documents" className="text-link">
        Back to DocChat
      </Link>
    </main>
  )
}
