/** Base URL of the backend. Defaults to the page origin (same-origin deploys, jsdom tests). */
export function apiBase(): string {
  const v = import.meta.env.VITE_API_BASE_URL
  return (v && v.length > 0 ? v : window.location.origin).replace(/\/+$/, '')
}
