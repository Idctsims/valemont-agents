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

## Layout

- `src/proxy.ts`: session refresh, owner gate and CSP nonce on every request
  (Next 16 renamed `middleware.ts` to `proxy.ts`).
- `src/lib/supabase/`: server, browser and proxy clients.
- `src/styles/tokens.css`: the only file allowed to hold raw colours or sizes.
