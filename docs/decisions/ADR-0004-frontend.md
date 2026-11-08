# ADR-0004 Frontend (P6): Vite + React + TS static SPA
Status: proposed

## Context
Spec P6: portfolio-grade, static, Vercel-hosted app; no login; anonymous bearer token per visitor from `POST /v1/auth/anonymous`; streamed agent steps + citations over SSE; talks only to `/v1`. P5 is in flight (T-028 rewrites auth, T-032 publishes `docs/openapi.json` + CORS), so auth must be isolated and the contract hand-typed first. Today's stream citations are doc-level `{doc_id, filename, span:str}`; non-stream are chunk-level `Citation`. Errors are `{detail, code}`; 429 codes `rate_limited|spend_cap_exceeded|budget_exceeded`.
No server-side secrets exist in the browser; the only env is `VITE_API_BASE_URL` (+ `VITE_MOCK`).

## Decision
Every new dependency below is pinned EXACT (no `^`/`~`). Versions are "verify at install": the implementer runs `npm view <pkg> version`, installs with `--save-exact`, and records the resolved versions in `web/README.md` and the ticket. Prefer latest stable major whose peer deps agree; do not use pre-releases.

| Area | Choice | One-line why |
|---|---|---|
| Framework | Vite + React + TypeScript (strict) SPA, static `dist/` | Spec-mandated; no server functions, cheapest hosting, no SSR surface. |
| Router | `react-router-dom` (library/declarative mode, `createBrowserRouter`) | Boring, well documented; routes `/documents`, `/chat/:sessionId?`, feature-flagged `/summary` `/sentiment` `/compare`. |
| Server state | `@tanstack/react-query` | Caching, retry, invalidation for documents/sessions; avoids hand-rolled state. Streaming chat state lives in a local `useReducer`, not the query cache. |
| Styling | Tailwind CSS (`tailwindcss` + `@tailwindcss/vite`), plain components, `clsx` | No UI kit, zero runtime, small CSS; accessible primitives hand-written (few screens). |
| OpenAPI client | `openapi-typescript` (types only, dev dep) + `openapi-fetch` (about 6 KB typed fetch) | Types generated from `docs/openapi.json` via `npm run gen:api`; thin runtime, our fetch wrapper handles auth. Until T-032: hand-typed `web/src/api/contract.ts`, replaced by generated file, same exported names. |
| Mock mode | `msw` (browser worker, started only when `VITE_MOCK=1`) | Per spec; same handlers reused by Vitest (node `setupServer`) and Playwright. Canned SSE via `ReadableStream` responses. Worker file excluded from non-mock builds by dynamic import behind the flag. |
| SSE parser | Hand-rolled (about 60 lines, `web/src/api/sse.ts`) over `fetch` + `ReadableStream` + `TextDecoder`, with `AbortController` | Our grammar is tiny (`event:`/`data:` JSON, blank-line frames, always ends with `final`); avoids a dependency. Must handle chunk boundaries splitting frames, CRLF, multi-line `data`, and a stream ending without `final` (treated as error). Unit-tested with split-byte fixtures. |
| Unit/component tests | Vitest + `@testing-library/react` + `@testing-library/user-event` + `@testing-library/jest-dom` + `jsdom` | Vite-native, fast, no paid calls. |
| E2E | `@playwright/test`, chromium only, against `VITE_MOCK=1 vite preview` | Spec; no backend needed; deterministic in CI. |
| Lint/format | ESLint (flat config, `typescript-eslint`, `eslint-plugin-react-hooks`, `eslint-plugin-react-refresh`) + Prettier; `tsc --noEmit` for `typecheck` | Standard Vite template set; `no-restricted-syntax` forbids `dangerouslySetInnerHTML`. |
| Runtime/pm | Node 22 LTS (`.nvmrc` + `engines`), npm with committed `package-lock.json`, `npm ci` in CI | Boring, no extra tool install; lockfile gives reproducibility. Vercel project Node set to 22.x. |

### Directory layout (`web/`, Vercel root)
`web/{package.json,vite.config.ts,tsconfig.json,vercel.json,index.html,.nvmrc,.env.example}`; `web/src/{main.tsx,App.tsx,routes/,pages/{Documents,Chat,Summary,Sentiment,Compare},components/,features/{documents,chat,sessions}/,api/{client.ts,contract.ts|schema.d.ts,sse.ts,errors.ts},auth/token.ts,mocks/{browser.ts,server.ts,handlers.ts,fixtures/},lib/,test/}`; `web/e2e/`. Feature code never imports `fetch` directly, only `api/client.ts`.

### Anonymous token module (`web/src/auth/token.ts`, the ONLY auth file)
- API: `getToken(): Promise<string>`, `refreshToken(): Promise<string>`, `clearToken()`. Nothing else in the app knows storage key, endpoint or header name.
- Storage: `sessionStorage` key `docchat.token` (value + `expires_at`); per-tab, gone on tab close; never `localStorage`, never logged, never in URL.
- Mint: `POST /v1/auth/anonymous`; single in-flight promise (dedupe concurrent mints). Pre-emptively refresh if within 30 s of `expires_at`.
- 401 handling lives in `api/client.ts` middleware: on 401 call `refreshToken()` then retry once; a second 401 surfaces `auth_failed`. Same for SSE start (retry before first byte only; never replay a stream mid-flight). 429 on mint maps to "too many visitors, try later".
- Documents/sessions are bound to the anonymous user; a re-mint after expiry loses access to old data. UI states "demo data resets (about 24 h)". If T-028 changes token shape/endpoint, only this file and mock handlers change.

### Safe text rendering rule
All model, retrieved, filename and error text is rendered as React text nodes only. Banned: `dangerouslySetInnerHTML`, `innerHTML`, HTML-capable markdown renderers (no markdown lib in v1; answers shown with `white-space: pre-wrap`). Backend already HTML-escapes stream text: render as-is, never decode entities into HTML. Any model-provided URL is shown as plain text, not an `<a>`; no auto-follow. Citation chips use `doc_id`/`filename`/span as text. Enforced by ESLint rule plus a component test with `<img onerror>` payloads.

### CSP and `vercel.json`
SPA rewrite `/(.*)` to `/index.html` (after static files); headers on all routes: `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self' <API_ORIGIN>; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`, plus `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy` empty. `<API_ORIGIN>` is the staging backend origin, templated at build (`scripts/gen-vercel-json` or hand edit, implementer chooses; never a wildcard). Mock build adds `worker-src 'self'` only for the mock/demo build. No inline scripts/styles (Tailwind emits a CSS file). Cache `assets/*` immutable.

### Chat/stream typing
Types are a union tolerating both citation shapes (`StreamCitation | Citation`) with a `normalizeCitation()` helper until the backend fix lands. Error mapping table in `api/errors.ts`: `rate_limited`, `spend_cap_exceeded`, `budget_exceeded`, `llm_unavailable`, `document_busy`, `auth_required`, `not_ready`, per-file upload codes, unknown -> generic message (never show raw `detail` HTML-unsafe; rendered as text anyway).

## Backend contract needs (new or changed P5 work; frontend tickets depend on these)
1. `POST /v1/auth/anonymous` (new P5 ticket, after T-028): no body; returns `{access_token, token_type:"bearer", expires_at}`; short TTL (e.g. 1 h, re-mintable); per-IP mint rate limit (429 `rate_limited`, honour trusted proxy header); mints a distinct anonymous `owner_id` under a dedicated demo tenant with its own per-user/tenant USD caps; document TTL cleanup (default 24 h) for anonymous owners.
2. `GET /v1/sessions` (list, `limit/offset`, owner-scoped, `{items:[{session_id,title|first_question,updated_at}], total}`) plus `GET /v1/sessions/{id}` messages, if not already in T-026. Verify in `docs/architecture/sessions.md`; add to P5 if missing.
3. CORS (T-032): `CORS_ALLOW_ORIGINS` contains the Vercel preview/production origins (exact, no `*` with credentials; no cookies used); `Access-Control-Allow-Headers` includes `Authorization, Content-Type`; methods incl. `DELETE`; preflight cached; SSE response must not be buffered by proxy (`Cache-Control: no-cache`, `X-Accel-Buffering: no`).
4. Stream citations at chunk level (`Citation` shape: `chunk_id`, `page/sheet`, `span{char_start,char_end}`) in the `citations` and `final` events (backlog item); frontend tolerates both until then.
5. `docs/openapi.json` (T-032) must include the SSE endpoint description, error schema `{detail, code}` and the new auth/session endpoints so `gen:api` is complete.
6. Auth errors: 401 body keeps `{detail, code}` with distinct `token_expired` vs `invalid_token` so the client re-mints only on expiry-type errors.

## Alternatives
- Next.js with a BFF and httpOnly cookie: best XSS posture for tokens, but needs server functions (spec: static, no server), cross-site cookie/CSRF handling, and a second hop that doubles SSE proxy and Vercel function timeout/cost. Rejected. Mitigation instead: short TTL, tenant-scoped caps, strict CSP, no HTML rendering.
- External IdP (Auth0/Clerk/Supabase): real login contradicts the no-accounts non-goal, adds vendor, cost and a dependency; rejected for a portfolio demo.
- Hand-typed client only: drift risk once T-032 lands; kept only as the interim.
- `@microsoft/fetch-event-source` / `eventsource-parser`: fine libraries, but our protocol is trivial and a lib adds supply-chain surface; revisit if parsing bugs recur.
- Axios / SWR / Redux / MUI / shadcn: heavier or more deps than needed; rejected on cost.
- pnpm/bun: faster, but extra tool install and Vercel/CI config; npm suffices.

## Consequences (cost, risk, reversibility)
- Cost: hosting is free-tier static; no LLM spend from the UI beyond backend caps; CI adds Node install (cached) plus about 1-2 min for lint/typecheck/Vitest, Playwright only on demand.
- New deps (all dev-time except runtime: react, react-dom, react-router-dom, @tanstack/react-query, openapi-fetch, clsx): add `typescript`, `vite`, `@vitejs/plugin-react`, `tailwindcss`, `@tailwindcss/vite`, `openapi-typescript`, `msw`, `vitest`, `jsdom`, `@testing-library/*`, `@playwright/test`, `eslint` stack, `prettier`. Run `npm audit --omit=dev` in CI.
- Risk: token in `sessionStorage` is readable by any XSS; mitigated by CSP, text-only rendering, short TTL, demo-tenant caps. Anonymous endpoint is an abuse target; mitigated by backend controls above. Risk accepted: tokens lost on tab close (per-tab data).
- Reversibility: high. Router/query/styling are local to `web/`; auth isolated in one module; generated client swappable without touching pages.
- Human action: add the web verify command to `.claude/kit.json`, set Vercel root `web/` and `VITE_API_BASE_URL`, and extend `.env.example` (frontend: `VITE_API_BASE_URL`, `VITE_MOCK`).

## Amendment 2026-11-08 (design chosen, contract settled)
- Design: `docs/design/README.md` + `docs/design/prototype.html` (Soft Product x Mail, light only). Tailwind theme must use its tokens.
- Types are generated from `docs/openapi.json` from the start (T-032..T-037 merged). `Citation.span` is an object; stream citations are chunk-level. No hand-typed contract, no `normalizeCitation()`.
- Fonts self-hosted, not Google-hosted, so CSP stays `font-src 'self'`: add `@fontsource-variable/dm-sans` and `@fontsource/jetbrains-mono` (exact pin at install, record versions). No external font request.
- Scope: client sends `doc_ids` per query; per-conversation scope lives in `sessionStorage` (API stores none).
- New backend need: T-048 `GET /v1/documents/{doc_id}/chunks/{chunk_id}` returns the passage text and highlight offsets for the source pane.
