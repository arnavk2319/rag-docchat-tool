/**
 * Tiny structural validator for the JSON schemas in docs/openapi.json (OpenAPI 3.1).
 * Supports the keywords the backend emits: $ref, type, anyOf, enum, required, properties, items,
 * minimum/maximum, minLength. Unknown keys are allowed (like the backend's default).
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

type Json = Record<string, unknown>

export const openapi = JSON.parse(
  readFileSync(join(process.cwd(), '../docs/openapi.json'), 'utf8'),
) as {
  components: { schemas: Record<string, Json> }
  paths: Record<string, Record<string, Json>>
}

function resolve(ref: string): Json {
  const name = ref.replace('#/components/schemas/', '')
  const s = openapi.components.schemas[name]
  if (!s) throw new Error(`openapi.json has no schema ${name}`)
  return s
}

function typeOf(v: unknown): string {
  if (v === null) return 'null'
  if (Array.isArray(v)) return 'array'
  if (Number.isInteger(v)) return 'integer'
  return typeof v
}

function matches(t: string, v: unknown): boolean {
  const got = typeOf(v)
  return got === t || (t === 'number' && got === 'integer')
}

/** Returns a list of readable problems (empty when valid). */
export function validate(schema: Json, value: unknown, path = '$'): string[] {
  if (typeof schema.$ref === 'string') return validate(resolve(schema.$ref), value, path)
  if (Array.isArray(schema.anyOf)) {
    const attempts = (schema.anyOf as Json[]).map((s) => validate(s, value, path))
    return attempts.some((a) => a.length === 0)
      ? []
      : [`${path}: matches no anyOf branch (${attempts.map((a) => a[0]).join(' | ')})`]
  }
  const errs: string[] = []
  if (typeof schema.type === 'string' && !matches(schema.type, value)) {
    return [`${path}: expected ${schema.type}, got ${typeOf(value)}`]
  }
  if (Array.isArray(schema.enum) && !schema.enum.includes(value)) {
    errs.push(`${path}: ${JSON.stringify(value)} not in enum ${JSON.stringify(schema.enum)}`)
  }
  if (typeof value === 'number') {
    if (typeof schema.minimum === 'number' && value < schema.minimum)
      errs.push(`${path}: below minimum`)
    if (typeof schema.maximum === 'number' && value > schema.maximum)
      errs.push(`${path}: above maximum`)
  }
  if (
    typeof value === 'string' &&
    typeof schema.minLength === 'number' &&
    value.length < schema.minLength
  ) {
    errs.push(`${path}: shorter than minLength`)
  }
  if (schema.type === 'object' && typeof value === 'object' && value !== null) {
    const obj = value as Json
    for (const r of (schema.required as string[] | undefined) ?? []) {
      if (!(r in obj)) errs.push(`${path}: missing required field "${r}"`)
    }
    for (const [k, sub] of Object.entries(
      (schema.properties as Record<string, Json> | undefined) ?? {},
    )) {
      if (k in obj) errs.push(...validate(sub, obj[k], `${path}.${k}`))
    }
  }
  if (schema.type === 'array' && Array.isArray(value) && schema.items) {
    value.forEach((it, i) => errs.push(...validate(schema.items as Json, it, `${path}[${i}]`)))
  }
  return errs
}

/** Asserts `value` conforms to components.schemas[name], with a message naming the contract. */
export function assertSchema(name: string, value: unknown): void {
  const errs = validate({ $ref: `#/components/schemas/${name}` }, value)
  if (errs.length) {
    throw new Error(
      `Contract drift: fixture does not match ${name} in docs/openapi.json (backend contract changed?)\n  ${errs.join('\n  ')}`,
    )
  }
}
