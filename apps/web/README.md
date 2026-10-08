# apps/web

Valemont Command: Next.js 16 (App Router), TypeScript strict, Tailwind v4,
Supabase Auth via `@supabase/ssr`. Owner-only.

It reads Supabase server-side with the owner's session and never writes ledger
tables; the Python workers in `workers/` are the only ledger writers
(CLAUDE.md §5).

## Run (PowerShell 5.1, from the repo root)

```
pnpm install
Copy-Item apps\web\.env.example apps\web\.env.local   # then fill it in
pnpm --filter web dev
```

`pnpm --filter web lint` runs ESLint plus the design-token and contrast checks.

## Generated files and keys (from `apps\web`)

- `src/generated/theme-colors.ts`: written from `tokens.css` before every
  dev, build and lint run (`scripts/gen-theme-colors.mjs`). Gitignored.
- `pnpm gen:icons`: re-renders the app icons from the tokens and Instrument
  Serif. Rerun after a token or design change, and commit the PNGs.
- `pnpm gen:vapid`: writes a VAPID key pair into `.env.local` and the root
  `.env`, printing no values. It refuses to rotate an existing pair, because
  that orphans every push subscription (`-- --force` to mean it).

## PWA

The service worker is `src/app/sw.ts`, served at `/serwist/sw.js`. It is off
in `pnpm dev`, so test it against `pnpm build; pnpm start`. It never caches
pages or data: only static build files, icons and `/offline`.

## Tests (Playwright, native Windows)

From `apps\web`. One-time browser install, then the suite. It builds and
starts a production server on port 3100 itself (or reuses one already there),
reading `.env.local` like the real app.

```
pnpm exec playwright install chromium
pnpm test:e2e
```

Two projects run every test: `desktop` and `phone` (390px). The smoke suite
needs no credentials and makes one deliberately failing sign-in per run.

The owner suite (`e2e/owner.spec.ts`: sign in, active pillar, theme
screenshots, log out) skips unless the owner's credentials are set **in the
shell session only**, never in a file:

```
$env:E2E_OWNER_EMAIL = 'you@example.com'
$env:E2E_OWNER_PASSWORD = '...'
pnpm test:e2e
Remove-Item Env:E2E_OWNER_EMAIL, Env:E2E_OWNER_PASSWORD
```

Its screenshots land in `e2e-screenshots/` (gitignored). The HTML report:
`pnpm exec playwright show-report`.

## Layout

- `src/proxy.ts`: session refresh, owner gate and CSP nonce on every request
  (Next 16 renamed `middleware.ts` to `proxy.ts`).
- `src/lib/supabase/`: server, browser and proxy clients.
- `src/styles/tokens.css`: the only file allowed to hold raw colours or sizes.
