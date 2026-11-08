# DocChat web

Vite + React + TypeScript SPA (ADR-0004). Node 22 (`.nvmrc`), npm.

```
cp .env.example .env.local   # VITE_API_BASE_URL, VITE_MOCK
npm ci
npm run dev | lint | typecheck | build
npm test -- --run
```

Only `VITE_API_BASE_URL` and `VITE_MOCK` may be read via `import.meta.env`.

## Pinned versions (exact, resolved at install 2026-11-08)

Runtime: react 19.3.1, react-dom 19.3.1, react-router-dom 7.18.5, clsx 2.1.1,
@fontsource-variable/dm-sans 5.3.0, @fontsource/jetbrains-mono 5.3.0.

Dev: vite 8.3.6, @vitejs/plugin-react 6.2.1, typescript 6.0.3 (npm view latest is 7.0.2; npm resolved 6.0.3 for peer agreement),
tailwindcss 4.3.5, @tailwindcss/vite 4.3.5, vitest 5.1.0, jsdom 29.1.1 (npm view latest 31.0.0; resolved by peers),
@testing-library/react 16.3.3, /dom 10.4.2, /user-event 14.6.8, /jest-dom 7.0.1,
eslint 10.14.0, @eslint/js 10.0.1, typescript-eslint 8.72.1, eslint-plugin-react-hooks 7.1.1,
eslint-plugin-react-refresh 0.5.7, globals 17.14.0, prettier 3.9.10,
@types/react 19.3.0, @types/react-dom 19.3.0, @types/node 22.20.5.

Added in T-039 (exact): @tanstack/react-query 5.107.1, openapi-fetch 0.17.0 (runtime); openapi-typescript 7.13.0, msw 3.0.4 (dev).
openapi-typescript declares peer `typescript@^5.x` while the repo uses 6.0.3, so `web/.npmrc` sets `legacy-peer-deps=true` (needed for `npm ci`). Remove it once openapi-typescript's peer range accepts TypeScript 6 (or if the repo pins TS 5.x).

Added in T-044 (exact, dev): @playwright/test 1.65.0 (chromium only).

## API contract and mocks (T-039)

- `npm run gen:api` regenerates `src/api/schema.d.ts` from `../docs/openapi.json` (commit the result; drift check = regen then `git diff --exit-code src/api`).
- `src/api/contract.ts` re-exports named types plus the SSE event union (SSE is not in OpenAPI).
- `src/auth/token.ts` owns the token (sessionStorage), mint endpoint and header; `src/api/client.ts` adds auth and the 401 re-mint-once logic.
- Mock mode: `npm run dev:mock` / `npm run build:mock` copy the MSW worker into `public/` (git-ignored) and set `VITE_MOCK=1`. Plain `npm run build` deletes it first so no mock code ships. Mock question keywords select SSE scenarios: `abstained`, `max_steps`, `max_tokens`, `max_usd`, `error`; default is `done`.

## Security headers and CSP (T-043)

- `vercel.json` is generated: `API_ORIGIN=https://api.example.com npm run gen:vercel` (add `-- --mock` for the mock/demo build, which only adds `worker-src 'self'`). The committed file uses the placeholder `https://api.docchat.example`; **before deploy, run the script with the real staging backend origin and commit the result** (a test keeps the committed file in sync with the generator for the placeholder). The origin must be a bare https origin; wildcards and paths are rejected.
- CSP has no `unsafe-inline`/`unsafe-eval`; source must not use inline `style=` props or `.style` writes (tested). Use Tailwind classes.
- Rendering rule: all model/user text is a React text node (ESLint bans HTML sinks; `src/test/xss.test.tsx` covers the payload matrix).
- After `npm run build`, `npx vitest run src/test/vercel-config` also checks `dist/index.html` and the bundle for secrets. Loose `grep sk-` matches React's `mask-type`; the test uses key-shaped patterns.

## E2E (T-044)

```
npx playwright install chromium   # once; browsers live in ~/Library/Caches/ms-playwright, never committed
npm run test:e2e                  # builds dist-e2e with VITE_MOCK=1, serves it, runs chromium
npm run test:e2e -- --repeat-each=3
```

Not part of `npm test`. `e2e/server.mjs` serves the mock build with the response headers of
`gen-vercel-json.mjs --mock` (localhost origin), so the CSP is enforced in the browser; the shared fixture in
`e2e/fixtures.ts` fails every test on any console error, page error or `securitypolicyviolation`
(expected ones are declared per test via `allow.console`). Specs override handlers at runtime through the
mock-only `window.__mock` seam (`src/mocks/browser.ts`; absent from the real build). No backend, localhost only.
Production headers on Vercel are still verified by the T-043 config tests and T-045 preview.
