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

Deferred to later tickets (not installed here): @tanstack/react-query, openapi-fetch, openapi-typescript, msw, @playwright/test.
