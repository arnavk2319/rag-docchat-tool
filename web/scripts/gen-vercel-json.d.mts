export const PLACEHOLDER_ORIGIN: string
export function parseApiOrigin(raw: string): string
export function buildCsp(apiOrigin: string, opts?: { mock?: boolean }): string
export function buildConfig(
  apiOrigin: string,
  opts?: { mock?: boolean },
): {
  rewrites: { source: string; destination: string }[]
  headers: { source: string; headers: { key: string; value: string }[] }[]
}
