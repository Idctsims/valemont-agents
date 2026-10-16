# The Arsenal — design (Chat 5, Phase 1)

Oct 15, 2026. Phase 1 only: the reference pool, verification, the daily drop
and the Power Word. Arm Me, saves, the quiz (Phase 2) and the Screening Room
hook (Phase 3) are out of scope; the schema leaves room for them
(`origin_kind`, `source_title_id`).

Code: `db/054_arsenal.sql`, `workers/arsenal/`, `apps/web/src/app/(app)/arsenal/`.
Voice: `prompts/arsenal_voice.md` (byte-identical copy in `workers/prompts/`).

---

## 1. Owner decisions (Chat 5 planning, Oct 15)

### The drop

- **Every day: 3 references in 3 different categories, plus 1 Power Word.**
- Categories and base weights:

  | Category | Base weight | Rule |
  |---|---|---|
  | `finance_business` | 1.0 | weighted up |
  | `history_military` | 1.0 | weighted up |
  | `film_tv` | 1.0 | weighted up |
  | `sports` | 1.0 | weighted up |
  | `music_culture` | 1.0 | weighted up |
  | `philosophy` | 0.6 | Stoics and strategists, applied only |
  | `literature_myth` | 0.3 | weighted down; only if instantly recognizable |

- **If a reference needs a footnote to land, it is out.**

### The card

| Field | What it holds |
|---|---|
| category | one of the seven above |
| title | the reference's name ("Crossing the Rubicon") |
| origin | at most 2 lines: where it comes from |
| meaning | what it means when you use it |
| deploy_line | one breath: the line you would actually say |
| room | `boardroom` \| `pitch` \| `negotiation` \| `homies` |
| source_work | always shown: "from The Godfather (1972)", "Julius Caesar, 49 BC", "Buffett, 1986 Berkshire letter" |
| quote | optional, at most 15 words, attributed to its work |
| source_url | the page the verification read (Wikipedia or Wikiquote) |
| verification | `verified` ("Checked") or `corrected` ("Corrected"); `unverified` never drops |

### The rooms, in the owner's context

- **boardroom:** Sail Beach Club co-founder and board calls; Valemont Group decisions.
- **pitch:** SBC investor meetings (hospitality, Miami); later VC/LP rooms for Sims & Vale Capital.
- **negotiation:** Perfect Timing label, brand and booking deals; freelance scope and pricing.
- **homies:** the friend group, family strategy, networking events, LinkedIn.

### The Power Word

- **The bar:** business register a sharp GP would say unprompted. *Asymmetric, inflection, provenance, fiduciary* set it. **Not SAT trivia.**
- **The card:** pronunciation; a two-line etymology (from kaikki/Wiktionary); meaning; room; and one sentence the owner would actually say in that room.

### Voice

- Axe-level confidence, Wags-level wit, and it lands in one breath.
- Never corny, never a poster, never memorized-sounding.
- It stretches vocabulary without sounding like a thesaurus.
- Lives in `prompts/arsenal_voice.md`, with a byte-identical copy in `workers/prompts/`. `tests/test_voice.py` fails on drift, the same pattern as `wags_voice.md`.

### Seeds: the owner's own canon

**Stored as status `seed`. They shape taste and NEVER appear in a drop.** Each is stored in its accurate form and verified through the same pipeline. Corrections known before verification:

- **Cortés scuttled his ships at Veracruz (1519); he did not burn them.**
- **Sun Tzu's text:** "victorious warriors win first and then go to war". "Every battle is won before it is fought" is a paraphrase.
- **The Godfather line:** "Never tell anybody outside the family what you're thinking again." (Vito to Sonny. Confirmed on Wikiquote in the Step 0 probe.)

Other corrections the pipeline finds are recorded in `arsenal_refs.verification_note` and listed in §8.

The 27 seeds: an offer he can't refuse; never tell anybody outside the family; Logan Roy's "you are not serious people"; Axe and Wags; Marty Byrde's composure; Neil McCauley's 30-second rule (Heat); Margin Call's "be first, be smarter, or cheat"; Michael Clayton the fixer; crossing the Rubicon; scuttling the ships; Pyrrhic victory; Hannibal over the Alps; Napoleon's speed; Sun Tzu; Machiavelli; The 48 Laws of Power; Marcus Aurelius; the Medici; Rockefeller; Buffett's "greedy when others are fearful"; Sam Zell; Jimmy Iovine; Saban's Process; Jordan's Last Dance; Kobe's Mamba Mentality; LeBron's long game off the court; Hamilton's comeback seasons.

### Repeats: never

A reference that has appeared in a drop never appears in one again. The database enforces it four ways:

1. `canonical_key` is UNIQUE on `arsenal_refs`, and `lemma` is UNIQUE on `arsenal_words`.
2. The drop trigger refuses any ref or word already in a drop, and any ref or word whose status is not `pool`.
3. A dropped ref or word is frozen: no UPDATE, no DELETE, no TRUNCATE.
4. Near-duplicates are caught at insert by `arsenal_near_dup(embedding, threshold)`: cosine similarity computed inside Postgres, so the vector goes in as a parameter and **is never read back**.

### Learning (light)

- Reactions: `love` | `not_interested` | `known`.
- **Love** and **Not interested** move category weights and room weights.
- **Known** excludes that reference, with no category penalty.
- Base weights come from the table above. There is no embedding profile.

---

## 2. Step 0 findings (Oct 15)

### Accounts and baseline

- `vercel whoami` printed `idctsims`.
- `tests/`: 973 OK.
- `tests_live -v`, metered: 392 tests OK, 0 skipped, ~2.42 MB read. Every connection was counted, including tests_live's own `psycopg.connect`, through a wrapper that sets `core/egress.py`'s `MeteredCursor` on every connection. A first run that patched only `Connection.connect` missed the pre-bound `psycopg.connect` and under-read ~0.13 MB.

### Claude pricing (`core/ai.py` PRICING, verified 2026-10-10)

- Sonnet 5.5: $2 in, $10 out, $0.10 cache read (per million tokens).
- Haiku 5.5: $0.10 in, $0.50 out (prompts at or under 100K tokens).
- **The batch rate is 50% of every rate.** `cost_usd(..., batch=True)` applies `BATCH_DISCOUNT = 0.5`, and `record_usage(batch=True)` stores `ai_usage.batch = true`.
- **Gap found:** `core/ai.py` had no way to *submit* a Message Batch, only to cost one. Phase 1 adds `submit_batch`, `batch_status` and `batch_results` there; it stays the only importer of the SDK.

**Estimates per call** (token counts are design estimates; the first real run replaces them, §9):

| Call | Model | Tokens (in / out) | Cost |
|---|---|---|---|
| Weekly pool batch: 30 refs + 10 words (2 requests in one batch) | Sonnet 5.5, batch | ~4,500 / ~10,000 (with thinking) | ~$0.055 |
| One verification | Haiku 5.5 | ~2,100 / ~150 | ~$0.0003 |
| One synchronous top-up: 6 refs + 2 words | Sonnet 5.5 | ~4,000 / ~3,000 | ~$0.04 |

- **Month:** about 4.3 batches (~$0.24) plus about 170 verifications (~$0.05), plus a rare top-up, comes to **about $0.30–0.40 a month**.
- The owner's target is ≤ ~$2 a month.

### Embeddings

- **Reused as is:** `core/embed.embed()` calls the `embed` Edge Function, which runs gte-small: 384 dimensions, unit length, batches of 8.
- Stored as `extensions.halfvec(384)`, written as pgvector text, exactly like `titles.embedding` (db/053).
- **No new model.**
- Near-duplicates are checked by `extensions.cosine_distance(halfvec, halfvec)` (pgvector 0.8.2, read 2026-10-15) inside `arsenal_near_dup`.
- **The threshold starts at cosine 0.92**, a little under the feed's same-story join (0.94). It is a starting value to calibrate, not a measured one.

### Free sources

Probed once each from the worker's HTTP path (httpx, 10 s timeout), with User-Agent `ValemontCommand/1.0 (+https://valemont-command.vercel.app/privacy)`.

**Wikipedia REST page summary** (`en.wikipedia.org/api/rest_v1/page/summary/{title}`)
- Result: HTTP 200, JSON, 2.7 KB for "Crossing the Rubicon".
- Keys: `title`, `description`, `extract` (448 characters), `content_urls.desktop.page`, `revision`, `timestamp`, and others.
- Use: the extract is the source text for history, sports, business, philosophy and music cards.

**Wikiquote MediaWiki API** (`en.wikiquote.org/w/api.php?action=parse&prop=sections|wikitext&formatversion=2&redirects=1`)
- Result: HTTP 200, 35 KB for "The Godfather".
- `sections` is a list of `{index, line, level}`: Vito Corleone, Peter Clemenza, Dialogue, Cast, Quotes about The Godfather, See also, External links.
- `wikitext` is 33 KB.
- Quotes are `*` and `:` lines, and dialogue is written `:'''Name''': line`.
- "Never tell anybody outside the family what you're thinking again." is present.
- Use: sections whose heading reads Disputed, Misattributed, About, "Quotes about", Cast, See also or External links are dropped before matching.

**kaikki.org per-word JSONL** (`kaikki.org/dictionary/English/meaning/{w[0]}/{w[:2]}/{word}.jsonl`)
- Result: HTTP 200, one JSON object per line (one per part of speech).
- Keys: `word`, `pos`, `senses`, `sounds` (`ipa`), `etymology_text`, and others.
- Sizes: asymmetric 7.7 KB, fiduciary 12.8 KB, provenance 22 KB, inflection 30 KB.
- **The pattern held for all 4 words probed.** The `.json` variant answers 404.

**Wiktionary REST definition** (`en.wiktionary.org/api/rest_v1/page/definition/{word}`)
- Result: HTTP 200, 1.1 KB.
- **It has definitions only: no etymology, no IPA.**
- So it is a fallback for "does the word exist" only. A word without a kaikki etymology is `unverified` ("no etymology source").

---

## 3. Data (db/054)

All tables have RLS on, anon revoked, the read-only role reading everything, and `is_test` on every table.

**Owner access by table:**
- **Worker-written content** (`arsenal_refs`, `arsenal_words`, `arsenal_pool_runs`): the owner reads only, through `is_owner()`, the db/051–053 pattern for worker-written tables. These have no `owner_id`: the content is not the owner's writing.
- **`arsenal_drops`:** carries `owner_id`, stamped by trigger from `app_settings`. The read policy is `owner_id = auth.uid()`.
- **`arsenal_reactions`:** the owner reads and inserts her own, `owner_id = auth.uid()`.

### arsenal_refs: the pool and the seeds

- **Immutable fields:** `canonical_key` (UNIQUE, snake_case), `is_test`, `origin_kind`, `created_at`.
- **Content fields** (the card fields, plus `quote_by` and `source_page`) may change only while `status = 'pool'` and `verification = 'pending'`. That is the verifier's correction step.
- **Status moves:**
  - `pool` → `dropped`, only when a drop row contains the ref;
  - `pool` → `rejected`;
  - `seed`, `dropped` and `rejected` never change.
- **Verification moves:** `pending` → `verified` | `corrected` | `unverified`, once.
- **The embedding** is set once.
- **No DELETE. No TRUNCATE.**
- `quote` is at most 15 words, by CHECK.

### arsenal_words: the Power Word pool

- Same lifecycle as refs. `lemma` is UNIQUE.

### arsenal_drops: append-only

- **One real drop per owner per date.** Test drops are not unique.
- `ref_ids` holds exactly 3 distinct ids, plus `word_id`.

**The guard refuses:**
- a ref or word already in any drop;
- a ref or word whose status is not `pool`, which covers seeds;
- verification that is not `verified` or `corrected`;
- fewer than 3 distinct categories;
- a real drop holding a test row, or the reverse.

**After insert:** it marks the ref and the word `dropped`.

### arsenal_reactions: append-only

- One row per tap: `love` | `not_interested` | `known` | `none` (`none` is an un-tap). The latest row per target counts.
- **`is_test` is stamped from the target**, so test reactions never train the real weights.

### arsenal_pool_runs: append-only

One row per event:

| `status` | Written when | What it carries |
|---|---|---|
| `submitted` | a batch is submitted | batch id, model, requested counts |
| `collected` | its results are read | inserted, duplicate and invalid counts, cost |
| `verified` | the run's last pending item settles | verified, corrected, unverified and rejected counts |
| `failed` | the batch errored or expired | the reason |

A top-up is `kind = 'topup'`, with no batch id.

### Functions

- `arsenal_near_dup(halfvec, threshold)` returns a boolean. It compares against real refs that are seeds, dropped, pending, verified or corrected.
- `arsenal_weights()` returns, per category and per room: base, loves, not_interested, weight. **weight = base × clamp(1 + 0.15·loves − 0.15·not_interested, 0.3, 2.0)**, counting only the latest real reaction per target. `known` and `none` count nothing. Words feed room weights only.
- `arsenal_pick_drop(date, with_tests)` is the picker, below. It returns the drop's id, and returns the existing id when that date already has a drop.
- `add_test_arsenal_ref`, `add_test_arsenal_word` and `add_test_arsenal_drop`: the verified e2e marker only (db/051 `e2e_marker_ok`).
- `ctx_arsenal()` (Wags, not a brief section): today's drop, the last 7 days, and the weights.
- `ctx_arsenal_brief()` (a brief section, one line): the Power Word, plus the strongest ref of today's three (highest category × room weight) with its room. NULL when today has no drop.
- Registry rows `arsenal` and `arsenal_brief`, sort 60.

### The picker (SQL, one implementation)

1. **Eligible:**
   - status `pool`, verification `verified` or `corrected`, `is_test = with_tests`;
   - not reacted `known` or `not_interested` (latest reaction).
   - So a real pick never sees a test row, and a test pick never sees a real one.
2. **Categories:** one weighted draw per category with eligible refs, key = −ln(u) / category_weight (Efraimidis–Spirakis); the 3 smallest keys win.
3. **Refs:** within each chosen category, one weighted draw by room weight.
4. **Slots:** the first ordering of the three, by draw order, in which no slot repeats yesterday's category in that slot. With 3 distinct categories, one always exists.
5. **Word:** a uniform draw among eligible words.
6. **Short pool:** fewer than 3 categories, or no word, raises `arsenal_pick_drop: the verified pool is short`.

---

## 4. Worker (`workers/arsenal/`)

All SQL is in `core/ledger.py`, and Claude is called only through `core/ai.py`. Fetches use the User-Agent above, a 10 s timeout, and back off on 429 (Retry-After, at most 2 tries). Every job runs through `run_tracked`, so its INFO line carries RSS and "~X KB read".

### arsenal_pool (pool.py)

- **When:** Sundays 03:30 Chicago, and at boot.
- **It submits when** no batch is open AND either:
  - the last submission is 6+ days old (the Sunday run), or
  - the pool is short: under 21 refs or under 7 words, counting verified, corrected and pending.
- **What it submits:** ONE Message Batch (Sonnet, non-critical) with two requests:
  - 30 refs, split across categories by the current weights (largest remainder);
  - 10 words.
- **The cached system block:** the voice file, the rooms, the categories, the seeds as "taste, never repeat", and the last 60 canonical keys.
- **The answer:** structured JSON, one object per card, including `source_kind`, `source_page` and `source_work`.
- **Each run also:**
  - embeds any seed without an embedding;
  - re-queues verification for anything still `pending` after 24 h.

### arsenal_pool_collect (collect.py)

- **When:** every 10 min. With no open batch it is one tiny read.
- **When the batch has ended:**
  1. records usage per result (`ai.record_usage(batch=True)`);
  2. validates each card; an invalid card is logged and counted, never an error;
  3. embeds `title — source_work`;
  4. skips near-duplicates and UNIQUE conflicts, logged and counted, not errors;
  5. inserts the rest `pending`;
  6. queues one `arsenal_verify` job per item;
  7. writes the `collected` row.

### arsenal_verify (verify.py, `job_queue` kind `arsenal_verify`)

One item per job, through `run_tracked`, so it has its own `job_health` row and its own RSS and KB line.

**For a ref:**

1. **Fetch.** Wikiquote for `film_tv` and for any card with a quote; the Wikipedia summary otherwise (and as Wikiquote's fallback for the origin). Wikiquote keeps only quote sections that are not Disputed, Misattributed, About, Quotes about, Cast, See also or External links. A page that is still unreachable after 2 tries makes the ref `unverified` ("source unreachable").
2. **Match the quote.** The quote is normalized (case, punctuation, curly quotes) and fuzzy-matched against the sourced lines (rapidfuzz `partial_ratio` ≥ 90 on lines at least as long as the quote).
   - **No match:** the quote is dropped and the card stays as a described move, `corrected`, with a note.
3. **Judge.** A Haiku judge (non-critical) reads the trimmed source text (≤ 6,000 characters, ~1,500 tokens) and the card's origin and source_work. It answers `supported` | `corrected` (with fixed wording) | `contradicted` | `insufficient`.
4. **Map:**

   | Judge verdict | Result |
   |---|---|
   | supported | `verified` |
   | corrected | `corrected` (the fixed wording replaces origin and source_work) |
   | contradicted, insufficient, or unreachable | `unverified`, with the reason |

   **The worse of the quote step and the judge wins.** Unverified never drops.

**For a word:**
- kaikki first. The etymology (two lines, at most 200 characters, cut at a sentence) and the pronunciation (the first IPA, US-tagged preferred) come from the source, never from the model.
- With no kaikki entry or no etymology, the word is `unverified`.

### arsenal_drop (drop.py)

- **When:** daily 05:30 Chicago, and at boot when today has no drop.
- **Normally zero AI calls:** `arsenal_pick_drop(today)`.
- **When the verified pool is short** (fewer than 3 categories with an eligible ref, or no eligible word): ONE synchronous Sonnet top-up (non-critical) of 6 refs in the missing categories plus 2 words. They go through the same validation and near-duplicate check, are verified inline by the same code path, and then the pick runs. The top-up is logged, with a `topup` pool-run row.
- **Still short after that:** the job raises (job_health records it, the health monitor pushes).
- The 06:15 Morning Brief reads `ctx_arsenal_brief`.

### Scripts (from `workers/`)

- `-m scripts.arsenal_pool --now`: submit, wait for the batch, collect, and verify everything inline. Prints counts and cost only.
- `-m scripts.arsenal_drop --date YYYY-MM-DD`
- `-m scripts.arsenal_check`: prints dropped refs that repeat (must be 0), seeds ever dropped (must be 0), verification counts by status, and today's drop ids.

### Egress

**What is read:**
- `arsenal_pool`: pool counts, run state, the last 60 keys, and the seeds' compact text.
- `arsenal_pool_collect`: the open batch row only.
- `arsenal_verify`: one row.
- `arsenal_drop`: counts, then one id back.
- `ctx_arsenal_brief`: a few hundred bytes.

**No query reads a vector.** The seed list and the weights are cached in worker memory for each run.

---

## 5. Web (`/arsenal`, nav Culture → The Arsenal)

- **Today:**
  - the Power Word hero: word, pronunciation, etymology, meaning, room, the sentence;
  - then the 3 cards: category label, title, the source_work line, origin (2 lines), meaning, the deploy line set as the emphasised line, a room chip, a "Source" link, and a small Checked or Corrected tag.
- **Reactions:** Love, Not interested, Already know it. Server actions return `{ok, error}`. "Already know it" hides the card into an "Already known" fold, with undo.
- **Folds:**
  - Past drops, by date;
  - Unverified: flagged, with the reason, never styled like a checked card.
- **Test rows** show only to a request with the verified e2e marker.
- Tokens only, phone-first, one-handed.

---

## 6. What Phase 1 does not do

- Arm Me, My Arsenal saves, and the weekly quiz (Phase 2).
- Finished shows drafting cards: Phase 3. `source_title_id` (a nullable FK to `titles`) exists from db/054 and stays NULL; Phase 3 widens `origin_kind` to `arm_me` and `title_hook`.
- Any embedding profile or vector read-back.

---

## 7. Open values to calibrate

- **The near-duplicate threshold, 0.92.** The first two pool runs' skipped near-duplicates are logged with their titles; check them for false merges.
- **The reaction step, ±0.15 per tap**, with clamp [0.3, 2.0]. Revisit after two weeks of reactions.

---

## 8a. Pre-merge record (Oct 16)

- **db/054 applied 04:36:56 UTC** (the second paste) and verified via db_inspect. The record is in CLAUDE.md, Current State.
- **Owner changes before merge (Oct 16):**
  1. The drop defers while a batch is open or under 2 h old. It retries every 30 min to 21:30 Chicago, and stops waiting from 21:00.
  2. The top-up verifies inline before the pick. With nothing verified it fails loudly (ERROR log and `DropFailed`).
  3. Every near-duplicate skip is logged with its cosine and the matched key, and the distribution goes into the pool run's note. The threshold stays at 0.92.
- **Found by `tests_live`:** `ledger.arsenal_near_dup` bound its threshold as double precision against db/054's `real`. The first batch would have failed on its first card. Fixed with a `::real` cast.
- **`tests/`: 1021 OK.** Commit 1451296's message says 1022; that was a miscount.
- **`tests_live -v`, metered: 409 tests OK, 0 skipped, ~2.43 MB read** (2,492 KB). That includes `test_arsenal_sql`, 17 tests.
- **Screenshot gate, phone Night: conditionally approved (owner, Oct 16).** PR #53 had already merged (05:03:43 UTC Oct 16), so these fixes ship in a follow-up PR (`chat5-arsenal-gate-fixes`):
  1. `screens-arsenal` hides the fixed dock during the capture only (`page.screenshot`'s `style`). The app's dock is unchanged.
  2. The reaction buttons show short labels under the icons: Love / Not for me / Know it. Their accessible names are unchanged (Love / Not interested / Already know it). Tokens only; each button is at least 44 px.
  3. The Power Word gets Already know it. It moves into the "Already known" fold, with Undo. The picker already excluded a known word, as it excludes a known reference (db/054, no migration). That is now tested: `test_a_known_word_is_never_picked_like_a_known_ref`, plus two new `arsenal.spec` tests (word known/undo; labels, names and 44 px).
  4. Fold chevrons: an empty Past drops or Unverified fold is locked closed (`Fold lockWhenEmpty`). Every fold now shows the same right-pointing chevron when closed. Before, the gate had opened the empty folds (down chevron, a double rule) and left the filled ones closed.
- **After the fixes, `tests_live -v`, metered (both `psycopg.connect` and `Connection.connect`): 410 OK, 0 skipped, ~2.64 MB read** (2,699 KB, 735 s). `tests/`: 1021 OK.

## 8b. Gate, round 2 (owner, Oct 16)

UI approved pending these fixes (same follow-up PR):

- **UI.** (1) `screens-arsenal` also hides the sticky phone header in captures (`header.sticky`; the app is unchanged). (2) The reactions sit on their own row, below the room chip and Source, on every card and the Power Word. (3) An unverified reason is muted (`text-text-muted`), never danger red (red is for real losses). It is clamped to two lines, wrapped at words, with a "more" toggle (`UnverifiedReason`). The Unverified fold starts closed. (4) The Not interested button's accessible name is "Not for me", matching its label (the reaction value stays `not_interested`).
- **Content.**
  - (5) The outcome check runs in the pool prompt (rules and voice) and in the judge. A card whose real ending undercuts its line is reframed (corrected, with `fixed_meaning` / `fixed_deploy_line`) or cut (unverified). Either way its note starts `outcome_conflict`.
  - (6) A film or TV quote credits the character and the work: `cards.credit_film_quote` turns "Sean Parker" into "Sean Parker (as written in The Social Network)". With no speaker to credit, or a credit over 80 characters, the quote goes and the card stays. The rule is in the voice (`VOICE_VERSION 2026-10-16.1`) and the rules block.
  - (7) **db/055** (written, not pasted): the picker takes the trio with the most distinct rooms, then the fewest of yesterday's rooms, then the draw. Slots avoid yesterday's category and, when possible, its room. The Power Word takes the room the references use least.
  - (8) **db/055**: `arsenal_words.status` gains `seed`; inflection, provenance and fiduciary (all in the pool) become seeds. asymmetric had already dropped (drop 61, Oct 16) and stays dropped. The prompt lists the seed words as KNOWN; `collect.blocked()` refuses them; the voice no longer offers the four as examples. That example list is why the first batch returned exactly those four.
- **Verifier retrieval (the judge not loosened).**
  - (9) Pages come from MediaWiki search for the work with its person (or its own context, for disambiguation: Moneyball the book) and the title, and from Wikiquote for the quote's phrase. Each page is split into sections. The judge reads the lead plus the best-matching sections (`sources.excerpt`), not the first 5,000 characters.
  - (10) `insufficient` tries one second source; `contradicted` and an outcome conflict never retry.
  - **Tightened, found in the dry run below:** Haiku used `corrected` to trim claims a source never mentioned. The judge now answers `covers_every_claim`, and `verify.held()` turns supported or corrected without it into insufficient. The prompt also says a fix comes only from the excerpt.
- **(11) Re-verification of the 8 unverified, DRY RUN only** (`scripts.arsenal_reverify`). db/054's guard lets verification settle once, so writing these verdicts needs an owner decision. Final run, 2026-10-16 ~19:45 UTC:

  | id | card | before | after (dry run) | page read |
  |---|---|---|---|---|
  | 190 | Bezos's Day 1 | insufficient | insufficient (2 sources) | Jeff Bezos, Andy Jassy |
  | 192 | Buffett's Ovarian Lottery | insufficient | insufficient (2 sources) | Warren Buffett, Bill Gates |
  | 201 | Sell me this pen | insufficient | contradicted (the note says insufficient fits better) | The Wolf of Wall Street (2013 film) |
  | 206 | Moneyball | no page | **corrected** ($44M payroll, 2002 playoffs; full book title) | Moneyball: The Art of Winning an Unfair Game |
  | 209 | I'm a business, man | insufficient | insufficient (2 sources) | Diamonds from Sierra Leone, Jay-Z |
  | 212 | Dr. Dre's The Chronic | contradicted | insufficient (the Beats sale is not on the album's page) | Dr. Dre discography, The Chronic |
  | 215 | Seneca on luck | insufficient | contradicted (stays unverified, as expected) | Seneca the Younger |
  | 216 | What is up to us | insufficient | **verified** | Enchiridion of Epictetus |

- **Tests after round 2:** `tests/` 1038 OK (17 new). `tests_live -v`, metered: 410 OK, **1 skip, named: `RoomsAndWordSeeds` "db/055 not pasted"** (its 4 tests run once db/055 is in), ~2.64 MB read (2,707 KB, 735 s). `pnpm lint` and `tsc` clean. The owner-run `arsenal.spec` and `screens-arsenal` are pending.

  **2 of 8 clear.** Haiku's answers vary between runs. Three dry runs were made: the first, before the coverage guard, "cleared" five by trimming claims. The verdicts on 201 and 215 swing between contradicted and insufficient (both unverified). Second sources are sometimes off-topic (Andy Jassy, Bill Gates); the judge then answers insufficient. Cost of the three runs: 47 Haiku calls, $0.019522.

## 8c. Round 3 (owner, Oct 16)

- **db/055 amended before the paste.**
  - (a) ONE re-verification of an unverified pool reference or word, only through `arsenal_reverify_ref` / `arsenal_reverify_word` (the worker's; nobody else executes them). Each one is recorded in `arsenal_reverifications`: append-only, TRUNCATE-guarded, UNIQUE per target; it holds who, the database role, when, and the old -> new verification and note. Every other "settles once" rule stands.
  - Item 11 is written through it in the migration: **206 corrected** and **216 verified**; the other six stay unverified.
  - **206's correction drops the 20-game winning streak of 2002.** The streak is true; the judge left it out because the book's page does not mention it.
  - (b) `arsenal_holds`: append-only, TRUNCATE-guarded, a snake_case reason plus a detail. The picker skips held items, and the worker's pool status never counts them as ready.
  - **The first paste failed and applied nothing** (42P01, `relation "public.arsenal_reverifications" does not exist`, in `arsenal_words_guard`). Section 2's `UPDATE` (the example words → seed) fired the new words guard, which read the record table created only in section 3. Read back via db_inspect: no log row, no new table or function, the status CHECK unchanged, the words still `pool`, 206 and 216 still unverified, all three guards still db/054's. Fixed by creating the table first (section 0). Both guards now read it only when the re-verify setting names the row. **Rule: a trigger function that a later statement of the same file fires must find every table it reads already created.**
  - The self-test covers both, plus item 11's state. Every `INSERT` was checked mechanically. One flag was the checker's own (an `ARRAY[...]` in the picker's insert, 5/5 as in db/054).
- **Temperature 0 is not available.** Haiku 5.5 rejects non-default sampling with a 400 (the API reference), and the installed SDK's `create()` has no `temperature` argument: a probe on 2026-10-16 failed before reaching the API. The judge (item 4) is unchanged, held by `covers_every_claim`. The item 5 outcome check reads each card's best page `OUTCOME_SAMPLES` = 3 times and holds on a strict majority. **Owner decision pending:** the vote, a different model, or a single read.
- **Item 5 over the existing pool:** `scripts.arsenal_outcome_check` (`--dry-run` writes nothing). It covers every real, undropped, verified or corrected reference and holds each conflict with reason `outcome_conflict`. It runs after db/055 is pasted, before the 05:30 drop. Results: pending.
- **Item 6 at render time:** `quoteCredit` (`lib/arsenal/types.ts`) shows a film or TV speaker as "Sean Parker (as written in The Social Network)" on every card, old and new. The generator rule stays. The same cases are in `e2e/arsenal-unit.spec.ts` and `tests/test_arsenal.py` `FilmQuoteCredit`.
- **Pool size: 36 references and 12 words a batch** (`REFS_MAX_TOKENS` 14,000). Estimate from the first batch's tokens, at the batch rate:

  | Part | Basis | Estimate |
  |---|---|---|
  | References | 7,352 output tokens for 30, so ~8,800 for 36 | ~$0.044 |
  | Words | 873 output tokens for 10, so ~1,050 for 12, plus the cache write | ~$0.009 |
  | Verification | ~1.6 Haiku reads a reference at ~$0.00042 each (the dry runs' 47 calls) | ~$0.024 |
  | **Total** | | **~$0.08 a weekly batch** (the first was $0.051) |

  The outcome check over a week's pool adds ~36 × 3 × ~$0.0004 ≈ $0.04 when it is run.
- **Near-duplicate threshold:** 0.92, unchanged until Sunday's batch (2026-10-18 03:30 Chicago). Its distribution goes next to the first batch's (min 0.79, p50 0.82, p90 0.87, max 0.91). Pending.
- **Today's Brief:** brief 1018 (2026-10-16, Sonnet, not a fallback) carries the `arsenal_brief` section: "Power word: asymmetric. Far more upside than downside. Test every move today against it." Delivery 379 → notification 466 was sent at 14:45:02 UTC (09:45 Chicago, not late).

## 8. Results

Filled in after the first production run (Step 5). Anything not run is pending.

**First production run, Oct 16 (read via db_inspect).** PR #53 merged at 05:03:43 UTC. Everything below came from the worker's scheduled jobs; `arsenal_pool --now` and `arsenal_drop` were not run by hand.

- **Pool:** batch run 14 submitted 05:11:57 UTC (`arsenal_pool`: "embedded 27; pool short: 0 refs, 0 words"), 30 refs and 10 words requested. Collected 06:02:37 (run 15): **30 refs and 10 words inserted, 0 near-duplicate skips, 0 invalid**. Verification closed 06:05:02 (run 16).
- **Verification of those 40:** refs 4 verified, 18 corrected, 8 unverified, 0 rejected; words 8 verified, 2 unverified (optionality, unimpeachable: no pronunciation in the source). The 27 `pending` refs are the seeds, which are never verified.
- **Near-duplicates:** 0 skips. Nearest-cosine distribution over all 30 candidates: min 0.79, p50 0.82, p90 0.87, max 0.91. The threshold of 0.92 sits just above the maximum. The floor is high (0.79), so gte-small separates these titles less than hoped; calibrate after the next batch.
- **Cost (`ai_usage`):** `arsenal_pool` 2 Sonnet batch results, $0.045415; `arsenal_verify` 29 Haiku calls, $0.005488. **Total $0.050903.**
- **arsenal_check (its queries through db_inspect):** repeated refs 0, repeated words 0, seeds dropped 0. Today (2026-10-16): drop 61, refs [194, 193, 202], word 37 (asymmetric), `topped_up` false, made at 10:30:01 UTC (05:30 Chicago). Its rooms were pitch, boardroom, pitch, and the word was pitch: the case db/055's room rule fixes.
- **KB read (`job_health`, each job's last run only):** `arsenal_pool` ~5 KB; `arsenal_pool_collect`, `arsenal_drop`, `arsenal_verify` ~0 KB. The day's egress (19:06 UTC) was ~18.7 MB, with no Arsenal job in the top four. Per-run history is in the Railway logs and was not read.
- **Worker RSS:** 141 MB at rest (heartbeat 19:18 UTC); the job peak was 149 MB during verification (06:05 UTC). This is one reading, not the 6 h curve the RAM rule asks for.
