# apps/web

Placeholder. The Next.js app (App Router, TypeScript, Tailwind) arrives in
Chat 1, Phase 2 — see `docs/MASTER_PLAN.md` §6.

It reads Supabase server-side with the owner's session and never writes ledger
tables; the Python workers in `workers/` are the only ledger writers
(CLAUDE.md §5).
