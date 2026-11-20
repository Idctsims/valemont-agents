# M1b draft: E1's window at 100%

**APPROVED as drafted (owner, 2026-11-20).** The decision rule below is frozen
by this commit, before any M1b call. The crawl runs only after the day's dry
runs, with no other Kalshi traffic from this machine. Nothing is frozen from
its result without the owner.

Drafted 2026-11-20, after M1 came back inconclusive (`m1_result_2026-11-20.md`:
+8.856¢, CI −3.85 to +17.05, n = 76). **No M1b number exists. This rule is
written before any is computed.**

## What M1b is

M1's reproduction step again, on every traded combo in E1's window instead of
M1's 10% ticker-hash sample:

- **Population:** `KXMVENBASINGLEGAME` combos with `close_time` 2026-04-03 to
  2026-06-01 (UTC), traded (volume > 0), settled. NBA single-game only. E1's
  README does not say its own population was NBA-only; ours is, and the report
  says so.
- **Definitions:** unchanged from `workers/scripts/research/m1_analysis.py`
  (maker edge per trade before fees, equal-weight mean over trades,
  volume-weight by `count_fp`, 95% bootstrap over combos, 500 resamples, seed
  20261119). The script's definitions, window and constants are not edited;
  M1b only points it at the larger sample. Its git hash goes on the output.
- **Reserve:** untouched. Everything closes before 2026-07-01, and the
  listing's reserve guard stays on (J1).
- **2025 NFL:** not in the population (J3 does not arise).

## What it costs (estimates from M1's own crawl, not measured)

Read from M1's listing state for `KXMVENBASINGLEGAME`: the census counts
**2,081 traded combos closing in April 2026 and 3,779 in May** (all traded,
before the 10% sample). M1 kept 620 in the window (1 more closed Apr 1-2), so
the window holds about **5,850 traded combos**. 76 of them already have trades
and candles on disk and are reused.

| Stage | Calls (est.) | Basis |
|---|---|---|
| Listing | ~60 pages, capped at 200 | The series lists latest close first. Window rows sit at kept positions 139-758 of 23,924 (1,821 pages in all). Stop at the first page whose rows all close before 2026-04-03. Hitting 200 pages stops the crawl, which is then reported. |
| Trades | ~5,800 | One `/historical/trades` call per combo (window mean 2.25 trades a combo, limit 1,000 a page) |
| Candles | ~15,000-35,000 | From one call per leg, 845 distinct legs in the 620 kept, rising sublinearly to ~8,000 legs; up to one call per (leg, fill cluster). Upper bound 5,850 × 2.61 legs × 2.25 trades ≈ 34,000 |
| **Total** | **~21,000-41,000** | |

**Hours:** M1's candle stage ran 53,702 calls in 6 h 11 m (0.41 s a call
effective, pace floor 0.25 s, 0 429s). At that rate M1b is **~2.5-4.7 hours**
over one or two nights. Zero Odds API credits. Same rules as Step 1 (§8):
resumable, adaptive pacing, stop and report if 429s pass 2%, raw rows only
under `research_data/`, never Supabase, and no other Kalshi crawler or dry run
alongside.

## Decision rule (frozen once the owner OKs this file, before any M1b number)

Let `m` be the equal-weight maker edge on the M1b sample, `[lo, hi]` its 95%
interval, and `n` the combos with at least one trade and a settlement.

1. **Minimum n: 3,000.** Below 3,000 combos M1b is **inconclusive** and
   reported as such. Nothing is frozen, and no third attempt is made without
   the owner.
2. **Reproduced:** `n >= 3,000` and `|m − 3.3¢| <= 1.0¢`, the same band as M1.
   Then M1's frozen rule continues as written: the cell table is computed on
   M1's existing sample (premium per cell with its interval; a cell under 300
   combos borrows the next shorter leg-count cell, labelled borrowed;
   cross-game rows flagged 2025-NFL / fitting-only / pre-maker-fee). The
   cells' n come from M1's sample, not M1b's: M1b covers one sport, one
   series and two months. **`expected_quote` is still not frozen until the
   owner reads that table.**
3. **Not reproduced:** `n >= 3,000`, `m` outside ±1.0¢ of +3.3¢, **and**
   `[lo, hi]` excludes +3.3¢. E1's headline does not hold on our data. No
   cell table, `expected_quote` stays "pending (M1)", and the fill model
   needs a new design (owner).
4. **Inconclusive:** `n >= 3,000`, `m` outside the band, but `[lo, hi]`
   contains +3.3¢. Reported as is. `expected_quote` stays absent; the owner
   decides.
5. The volume-weight figure (E1: +2.1¢) is reported beside it and decides
   nothing.

Reported with the output: the script hash, the date range, row counts
(listing pages, combos, trades, block trades, legs, candle rows, misses), the
429 share, and the mix by legs.

The owner's OK was given 2026-11-20; nothing was crawled before this commit.
