# Screening Room "More like…" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "More like…" box in the Screening Room: type a title, a person or a phrase, get ~15 films and shows with the same vibe, each verified on TMDB.

**Architecture:** TMDB `/search/multi` suggests anchors (titles and people) as the owner types. `POST /api/screening/vibe` asks Sonnet (structured output, through `lib/ai/claude.ts` and the budget) for 15 picks with a reason each, verifies each pick on TMDB by name, year and kind (unverified picks dropped), adds the owner's state per title (`v_title_state` + shelf) and stores the result in `screening_vibe_searches` (db/088, append-only). The same anchor within 30 days is served from the table, with no model call.

**Tech Stack:** Next.js 16 route handler, AI SDK v6 `generateText` + `Output.object`, TMDB v3, Supabase (owner session, RLS), PostgreSQL 17.

**Spec:** the design approved in session, 2026-11-21 (recorded in `docs/screening.md` §8 by Task 5).

## Global Constraints

- Model `claude-sonnet-5-5`; Haiku from 80% of the budget, refused at 100% (`checkBudget`, non-critical). Every real call writes `ai_usage` with purpose `screening_vibe`.
- 15 picks asked; shown: the verified ones, at most 15. Each reason ≤ 12 words.
- Rate limit: 10 searches in a rolling minute (rows in `screening_vibe_searches`, the database clock).
- Cache: same `anchor_key` and same `is_test`, newest row within 30 days.
- Mock model only when `mockAllowed(process.env)` AND the verified e2e marker; a marked request writes `is_test` rows only.
- No raw colours or arbitrary Tailwind values (`check:tokens`); no other module imports the Anthropic provider (`check:ai-import`).
- db/088: no BEGIN/COMMIT, a rolled-back self-test DO block, `migration_log` row; pasted by the owner.

## Review Focus

- A title Claude names that TMDB lists twice (a remake, a same-name show): the year and kind must decide; with no year match, the most popular same-kind hit within 1 year, else dropped.
- The anchor itself in the picks (Claude often repeats it): removed.
- The same title twice in the picks (film and series of one name are two titles; the same tmdb id twice is one): deduped by `kind:tmdbId`.
- A person anchor (director or actor): the prompt names them as a person with their department; their own films may be picks.
- Budget refused, TMDB down, or every pick unverified: a readable sentence, never a blank result and never a stored empty row.

---

### Task 1: db/088 `screening_vibe_searches`

**Files:**
- Create: `db/088_screening_vibe.sql`

**Interfaces:**
- Produces: table `screening_vibe_searches(id bigserial, anchor_key text, label text, picks jsonb, model text, ai_usage_id bigint NULL, is_test bool default false, created_at timestamptz)`; `anchor_key` CHECK `^(movie|tv|person):[0-9]{1,10}$|^text:.{2,120}$`; `label` 1–200 chars; `picks` a JSON array of 1–20 objects; `model` 1–60 chars. Index `(anchor_key, is_test, created_at DESC)` and `(created_at)`. Append-only (`arsenal_stamp_created`, `reject_mutation`, `reject_truncate`). RLS: authenticated SELECT and INSERT when `is_owner()`; anon none; `valemont_readonly` SELECT; sequence USAGE to authenticated (insert needs it).

- [ ] Write the file in db/058's shape, with a self-test that: inserts one row (stamped `created_at = now()`), refuses UPDATE, DELETE, TRUNCATE, an empty `picks` array, a bad `anchor_key`, and a 201-character label (6 refusals); then `selftest-rollback`; then the `migration_log` row `088_screening_vibe`.
- [ ] Run it on the scratch PG 17 (memory: scratch-postgres-start) as one transaction: NOTICE "088 self-test passed (rolled back)"; then mutate each CHECK and trigger one at a time and confirm the self-test goes red.
- [ ] Commit.

### Task 2: the pure core, `lib/screening/vibe.ts`

**Files:**
- Create: `apps/web/src/lib/screening/vibe.ts`
- Test: `apps/web/e2e/screening-vibe-unit.spec.ts`

**Interfaces:**
- Produces:
  - `type Anchor = { kind: "movie" | "tv" | "person" | "text"; id?: number; label: string; year?: number | null; dept?: string | null }`
  - `anchorKey(a: Anchor): string` (`movie:98`, `person:578`, `text:<lowercased, spaces collapsed, ≤120>`)
  - `vibePickSchema` (zod): `{ title: string, year: number | null, kind: "movie" | "tv", reason: string }`; `vibeSchema = { picks: VibePick[] }`
  - `vibeSystem(): string`, `vibePrompt(a: Anchor): string`
  - `type Candidate = { tmdbId: number; kind: "movie" | "tv"; name: string; year: number | null; popularity: number; posterPath: string | null }`
  - `matchPick(p: VibePick, hits: Candidate[]): Candidate | null`
  - `finalize(picks: { pick: VibePick; hit: Candidate | null }[], a: Anchor, max = 15): VibeCard[]` with `type VibeCard = { tmdbId; kind; name; year; posterPath; reason }`

- [ ] Tests: `anchorKey` for each kind and text normalization ("  90s   London ROM-COMS " → `text:90s london rom-coms`); `matchPick` exact year beats a more popular other year; ±1 year accepted when no exact; kind must match; names compared case- and punctuation-insensitively ("Ben-Hur" = "Ben Hur"); null when nothing within a year; `finalize` drops unverified, drops the anchor (same kind and id), dedupes by `kind:tmdbId`, keeps Claude's order, caps at 15; `vibePrompt` for a person names the department, for a title the year.
- [ ] Run `pnpm test:unit e2e/screening-vibe-unit.spec.ts`: red, then implement, then green.
- [ ] Commit.

### Task 3: TMDB anchors and the route

**Files:**
- Modify: `apps/web/src/lib/tmdb/client.ts` (add `searchAnchors(query, limit = 8)`: movies, TV and people from `/search/multi`, people with `known_for_department` and `profile_path`; add `searchKind(kind, name, year)`: `/search/movie` or `/search/tv` with `year`/`first_air_date_year` when given, returning `Candidate[]`)
- Create: `apps/web/src/app/api/screening/vibe/route.ts`
- Create: `apps/web/src/app/api/screening/anchors/route.ts` (GET `?q=`; owner only; `searchAnchors`)
- Modify: `apps/web/src/lib/ai/claude.ts` (`languageModel(id, mockFor: "wags" | "arsenal" | "vibe")`), create `apps/web/src/lib/screening/vibe-mock.ts` (doGenerate returning 4 picks: two real tmdb-findable titles, one invented title, the anchor itself)
- Modify: `apps/web/src/lib/screening/data.ts` (add `titleStates(supabase, keys: {kind, tmdbId}[], withTests)` → `Map<"kind:tmdbId", { shelf: Shelf | null; seen: boolean; reaction: string | null; providers: Providers | null }>` reading `titles` then `v_title_state`)

**Interfaces:**
- POST body `{ anchor: Anchor }`; response 200 `{ cached: boolean; label: string; cards: (VibeCard & { shelf; seen; reaction; services: string[] })[] }`; refusals are plain text with 400/402/403/429/502/503 like Arm Me.
- Order: owner → marker → body → cache hit (return, `cached: true`) → rate limit → budget → model → verify (TMDB lookups in parallel, at most 15) → none verified: 502 "Couldn't match any titles on TMDB. Try again." (nothing stored) → insert row → states → respond.
- `services`: names from `MY_SERVICES` for the title's stored providers when the title is in `titles`; otherwise empty (no extra TMDB call per card).

- [ ] Implement; `pnpm exec tsc --noEmit` and `pnpm lint` clean.
- [ ] Commit.

### Task 4: the UI

**Files:**
- Create: `apps/web/src/components/screening/vibe-box.tsx` (client: input → `/api/screening/anchors` debounced 250 ms → suggestion list with poster or photo, year or department; choose one, or Enter for text → POST vibe → loading state "Finding the vibe…" → card grid; recent searches chips from props)
- Create: `apps/web/src/components/screening/vibe-card.tsx` (poster, name, year, Film/Series, reason, service chip, Seen/Loved/shelf mark, `ShelfButtons compact`, link to `/screening/title/{kind}/{tmdbId}`)
- Modify: `apps/web/src/app/(app)/screening/page.tsx` (Shelves tab: "More like…" section above the search; recent = last 5 distinct labels from `screening_vibe_searches`)
- Modify: `apps/web/src/app/(app)/screening/title/[kind]/[id]/page.tsx` ("More like this" link to `/screening?like={kind}:{id}#vibe`, which pre-runs the box)

- [ ] View at 390 px, both themes, with a temporary unauthenticated preview (removed before commit).
- [ ] Commit.

### Task 5: owner e2e, docs, gate

**Files:**
- Create: `apps/web/e2e/screening-vibe.spec.ts` (owner, phone, mock model: a search shows the verified cards only, never the anchor; the same search again comes back `cached`; a person suggestion appears for a person query)
- Modify: `docs/screening.md` (§8), `CLAUDE.md` migration record after the paste

- [ ] `pnpm test:unit`, `pnpm lint`, logged-out `pnpm test:e2e e2e/screening.spec.ts`; `tests_live` is untouched by this branch except db/088 (a live test `tests_live/test_screening_vibe_sql.py`: insert as `_test`-style is_test row, refusals) after the paste.
- [ ] Commit; PR; merge on the owner's word.
