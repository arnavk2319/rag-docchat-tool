# Web deploy runbook (Vercel, T-045)

Frontend: `web/` (Vite SPA). Backend runbook: [deploy.md](deploy.md). CSP rationale: ADR-0004 "CSP and vercel.json".
Agents never run deploy commands; items marked **HUMAN** are done by the owner.

## 1. Vercel project settings (HUMAN, one time)

| Setting | Value |
|---|---|
| Root Directory | `web` |
| Framework preset | Vite |
| Node.js version | 22.x |
| Install command | `npm ci` |
| Build command | `npm run build` |
| Output directory | `dist` |
| Production branch | `main` (only promoted at Gate B) |
| Preview | every push to `dev` (and `t-*` branches if enabled) |

Env var (Project > Settings > Environment Variables, Preview scope): `VITE_API_BASE_URL` = staging API origin, e.g. `https://api-staging.example.com` (bare https origin, no path, no trailing slash). It is baked in at build time; changing it needs a redeploy. Never put secrets in `VITE_*` variables (they ship to the browser). Production scope gets the production API origin at Gate B only.

## 2. REQUIRED before deploy: generate vercel.json with the real API origin

The committed `web/vercel.json` uses the placeholder `https://api.docchat.example`; with it the browser CSP blocks every API call.

```bash
cd web
API_ORIGIN=https://api-staging.example.com npm run gen:vercel   # real staging backend origin
npm run check:vercel          # exits 1 if connect-src is still the placeholder
git add vercel.json && git commit -m "chore(web): set staging API origin in CSP"
```

Do not hand edit `vercel.json`. The origin must equal `VITE_API_BASE_URL`. Production needs its own generated file at Gate B (separate commit on the release path).

## 3. Backend side: CORS (HUMAN)

Set `CORS_ALLOW_ORIGINS` on the backend (see deploy.md env table) to the exact web origins, comma separated, no wildcard (`*` is ignored; empty means no CORS):

```
CORS_ALLOW_ORIGINS=https://docchat-staging.example.app
```

Vercel per-deployment preview URLs (`<project>-<hash>-<team>.vercel.app`) change every push, so do not list them. Instead use a stable alias: assign a fixed staging domain/alias to the `dev` branch (Project > Settings > Domains > branch assignment, e.g. `docchat-staging.example.app`) and list only that origin. Test the stable alias, not the hash URL. Production lists only the production domain. Redeploy/restart the backend after changing the variable.

## 4. Mock / demo deployment (no backend)

Separate Vercel project (or branch alias) with `VITE_MOCK=1` and build command `npm run build:mock`. Generate its config with the worker allowance:

```bash
API_ORIGIN=https://api-staging.example.com npm run gen:vercel -- --mock   # adds worker-src 'self'
```

`build:mock` copies the MSW worker into `public/` (git-ignored); plain `npm run build` deletes it so no mock code ships to staging or production. Never use the mock variant for production. Demo data is in-memory per page load: reset by reloading the page (clear site storage to drop the anonymous token).

## 5. Release flow

1. Merge to `dev` -> Vercel builds a preview; staging alias updates.
2. Run the smoke checklist (below) against the stable staging alias.
3. Production promotion (`dev` -> `main`, production domain, production env vars, production CSP origin) is **Gate B: human only**. Agents never push to `main` or promote.

## 6. Smoke checklist

Set `URL=https://docchat-staging.example.app` and `API=<staging api origin>`.

```bash
curl -sI $URL | grep -i -e content-security-policy -e x-content-type-options
#   expect CSP with connect-src 'self' $API, and nosniff
curl -s -o /dev/null -w "%{http_code}\n" $URL/chat/abc     # SPA deep link: 200
curl -sI -H "Origin: $URL" $API/health | grep -i access-control-allow-origin   # CORS echoes $URL
```

Browser (DevTools console open, expect no CSP violations):
1. Page load mints an anonymous token (network: token request 2xx).
2. Upload a small PDF/text file; it appears in the documents list and reaches ready status.
3. Ask a question: the answer streams token by token (SSE), ends with done.
4. Click a citation: the source pane opens with the cited span highlighted.
5. Sessions: new session appears in the list, switching loads its history, reload keeps it.
6. Mock demo only: loads and answers with no backend reachable.

## 7. Rollback

Vercel keeps every deployment. Instant rollback: Deployments > pick last good > "Promote to Production" (or "Assign domain" for the staging alias). CLI equivalent (HUMAN): `vercel rollback <deployment-url>`. Config rollback: `git revert` the vercel.json commit. Roll back the backend separately per deploy.md; keep the CSP origin and `CORS_ALLOW_ORIGINS` consistent with whichever pair is live.

## 8. Human-only checklist

- [ ] Create Vercel project and link repo (root `web`, Node 22.x)
- [ ] Set `VITE_API_BASE_URL` (and `VITE_MOCK` on the demo project)
- [ ] Assign stable staging alias/domain; production domain at Gate B
- [ ] Set backend `CORS_ALLOW_ORIGINS` to the exact origins
- [ ] Add a `.github/` CI job (agents cannot edit it): `cd web && npm ci && npm run lint && npm run typecheck && npm test -- --run && npm run build`, plus `npm run check:vercel` on the release branch
- [ ] Run `gen:vercel` with the real origin and commit before deploy
- [ ] Gate B production promotion
