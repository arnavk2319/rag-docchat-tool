// Logs must never contain secrets, env values, emails or file contents.
const PATTERNS: [string, RegExp][] = [
  ["email", /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/],
  ["anthropic/openai key", /\bsk-[A-Za-z0-9_-]{16,}/],
  ["github token", /\bgh[pousr]_[A-Za-z0-9]{20,}/],
  ["aws key", /\bAKIA[0-9A-Z]{16}\b/],
  ["private key", /-----BEGIN [A-Z ]*PRIVATE KEY-----/],
  ["db url with password", /\b[a-z]+:\/\/[^\s:@/]+:[^\s@/]+@/i],
  ["bearer token", /\bBearer\s+[A-Za-z0-9._-]{20,}/],
];

/** Returns the name of the first sensitive pattern found, or null. */
export function findSensitive(text: string): string | null {
  for (const [name, re] of PATTERNS) if (re.test(text)) return name;
  return null;
}
