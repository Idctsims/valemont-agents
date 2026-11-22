# Screening Room, Letterboxd-style Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Screening Room looks and works like Letterboxd: For you as posters only, the title page with a backdrop hero and Watch / (Watching) / Like / Watchlist buttons, and the shelves as three poster-grid tabs.

**Architecture:** `db/089` adds a `none` shelf move (append-only "off the shelf") and redefines `v_watch_current` and `v_title_state` so every reader treats a latest `none` as no shelf. The web gains one shelf action that sets or clears, a `TitleActions` row (tap a lit button to turn it off; Watch, Watching and Watchlist are one shelf, Like is the love reaction), a "⋯" menu with Not interested and Neutral, and TMDB details with the backdrop, tagline, runtime and director.

**Tech Stack:** Next.js 16, Supabase (RLS), PostgreSQL 17, TMDB v3.

**Spec:** the design approved in session, 2026-11-22 (five Letterboxd screenshots), recorded in `docs/screening.md` §9 by Task 5.

## Global Constraints

- Films: Watch, Like, Watchlist. Series: Watch, Watching, Like, Watchlist. Watch = Finished, Watching = Watching, Watchlist = Want, Like = the `love` reaction.
- Tapping a lit shelf button writes `none` (off the shelf); tapping a lit Like writes reaction `none`.
- Title page keeps, below the hero and buttons, only: the trailer, Tonight on your services, Other ways to watch, and the TMDB/JustWatch attribution. In the news and the Arsenal cards leave this page.
- For you: posters only, Film/Series tag under each; tap opens the title page.
- Shelves: Watching / Want / Finished tabs (`?shelf=`), poster grids, 3 across on the phone, 5 from `lg`; Finished newest first.
- Not interested and Neutral live in the title page's "⋯" menu.
- No raw colours or arbitrary Tailwind values; migration pasted by the owner, rolled-back self-test.

## Review Focus

- A title whose latest move is `none` must vanish from every shelf, Upcoming, release alerts and `ctx_screening`, and come back for For you (it is no longer "on a shelf").
- Watched then un-watched: `finished` (ever finished) stays true by design (recs keep it excluded; the Arsenal hook already fired); the shelf is empty.
- A series with no next episode, a film with no backdrop or no director: the hero degrades to the poster's colours, no broken image.
- A test title (e2e marker) on the new page and grids.
- The For you row with 0 recs still shows its empty sentence.

---

### Task 1: db/089 off-the-shelf

**Files:** Create `db/089_watch_status_none.sql`.
**Produces:** `watch_status.status` CHECK `want | watching | finished | none`; `v_watch_current` = the latest move per title, rows whose latest is `none` left out (same columns); `v_title_state.shelf` = `nullif(latest, 'none')` (same columns; `finished` unchanged: ever finished).
- [ ] Self-test: want then none (no current row, shelf NULL); none then watching (watching); finished then none (shelf NULL, finished true); `ctx_screening()` still answers; a bad status refused. Rolled back.
- [ ] Scratch PG 17 (s88 + 089): NOTICE; mutations red.
- [ ] Commit.

### Task 2: web shelf and reaction actions, TMDB extras

**Files:** `src/app/(app)/screening/actions.ts` (`setShelf({ target, shelf: Shelf | null })`), `src/lib/screening/types.ts` (`nextShelf(current, tapped): Shelf | null`), `src/lib/tmdb/client.ts` (`TitleDetails` gains `backdropPath`, `tagline`, `runtime`, `director` (film) or `creators` (series)), test `e2e/screening-letterboxd-unit.spec.ts`.
- [ ] Tests for `nextShelf` (tap lit → null; tap other → it) and a `credits` parse helper (`directorOf`).
- [ ] Implement; tsc, lint.
- [ ] Commit.

### Task 3: the title page

**Files:** `src/app/(app)/screening/title/[kind]/[id]/page.tsx`, create `src/components/screening/title-actions.tsx`, `src/components/screening/title-more.tsx` (⋯ menu).
- [ ] Hero backdrop (w1280, fades into the page), title, year · directed by, trailer button (to `#trailer`), runtime, poster at the right; tagline and overview; `TitleActions`; More like this; then trailer, services, other ways, attribution. Back arrow and ⋯ over the hero.
- [ ] View at 390 px (throwaway route), both themes.
- [ ] Commit.

### Task 4: For you posters and the shelf tabs

**Files:** `src/components/screening/for-you.tsx`, `src/app/(app)/screening/page.tsx`, create `src/components/screening/poster-grid.tsx`.
- [ ] For you: poster + Film/Series tag, a link each; the toast/undo/buttons go.
- [ ] Shelves: a Watching/Want/Finished segmented bar (links, `?shelf=`), the grid, empty sentences.
- [ ] View at 390 px.
- [ ] Commit.

### Task 5: specs, docs, gate

- [ ] Update `screening.spec`, `screening-recs.spec`, `arsenal-hook.spec`, `screens-*` to the new page (logged-out runs; owner specs not run).
- [ ] `docs/screening.md` §9; after the paste, CLAUDE.md migration record; full metered `tests_live -v`.
