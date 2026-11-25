"use client";

import { MagnifyingGlass } from "@phosphor-icons/react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { useBetting } from "@/components/betting/betting-store";
import { Segmented } from "@/components/betting/segmented";
import {
  PAGE_SIZE, SINGLE_TIERS, SINGLE_TIER_BAND, SINGLE_TIER_NAME, ageText, costText, edgeText, rowWords, singlesHref,
  stale, startText, toLeg, type SingleRow, type SinglesKind, type SinglesPage, type SinglesQuery, type SinglesSort,
} from "@/lib/betting/singles";

// The Props tab's interactive part (docs/props_tab.md): Props / Lines, the tier
// bar with counts, the sort, the search, the rows, the pages. Every filter is a
// URL (a reload or Back keeps it); a row's + Slip puts that side in the slip,
// which prices any games and sports as one combo. Nothing here bets.

const KINDS = ["prop", "line"] as const;
const KIND_NAME: Record<SinglesKind, string> = { prop: "Props", line: "Lines" };
const SORTS: { s: SinglesSort; face: string }[] = [{ s: "edge", face: "Best value" }, { s: "mult", face: "Biggest payout" }];

function Row({ r, sport }: { r: SingleRow; sport?: string }) {
  const { unit, legs, put, remove } = useBetting();
  const w = rowWords(r);
  const inSlip = legs.find((l) => l.t === r.m);
  const here = inSlip?.side === r.side;
  const edge = edgeText(r);
  return (
    <li data-testid="single" data-tier={r.tier} data-side={r.side} data-ticker={r.m}
        className="flex items-start gap-3 border-b border-border py-3.5">
      <div className="min-w-0 flex-1">
        <p className="truncate font-semibold text-text">{w.title}</p>
        {w.detail && <p className="text-sm text-text">{w.detail}</p>}
        {(w.game || r.start) && (
          <p className="truncate text-xs text-text-muted">{[w.game, startText(r.start)].filter(Boolean).join(" · ")}</p>
        )}
        <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-text-muted">
          {r.tier !== "kalshi" && (
            <span data-testid="single-source" className="label-mono rounded-tile bg-accent px-1.5 text-on-accent">
              {r.tier === "books" ? "books" : "ESPN"}
            </span>
          )}
          {r.p !== undefined && <span>fair {Math.round(r.p * 1000) / 10}%</span>}
          {edge && <span data-testid="single-edge">{edge}</span>}
          {r.age !== undefined && (
            <span className={stale(r) ? "font-semibold text-on-pace" : undefined}>
              {stale(r) ? "stale · " : ""}{ageText(r.age)}
            </span>
          )}
          {r.combo === "no" && <span>not in combos</span>}
          {r.combo === "yes_only" && <span>YES only in a combo</span>}
        </p>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1.5">
        <span data-testid="single-price" className="font-mono text-lg tabular-nums text-text">{costText(r, unit)}</span>
        <button type="button" data-testid="single-add" aria-pressed={here}
                aria-label={here ? `Remove ${w.title} from the slip` : `Add ${w.title} to the slip`}
                onClick={() => (here ? remove(r.m) : put(toLeg(r, sport, w)))}
                className={`tap inline-flex items-center justify-center rounded-pill border px-3.5 text-sm font-semibold transition-colors ${
                  here ? "border-accent bg-accent text-on-accent" : "border-border text-text hover:border-text"}`}>
          {here ? "In slip" : inSlip ? "Switch side" : "+ Slip"}
        </button>
      </div>
    </li>
  );
}

export function PropsView({ sport, q, page }: { sport?: string; q: SinglesQuery; page: SinglesPage }) {
  const router = useRouter();
  const [busy, start] = useTransition();
  const [text, setText] = useState(q.q ?? "");
  const go = (change: Partial<SinglesQuery>) => start(() => router.push(singlesHref(q, change)));
  const n = page.counts[q.tier] ?? 0;
  const from = q.offset + 1;
  const to = q.offset + page.rows.length;

  return (
    <section data-testid="props-list" aria-busy={busy} aria-label="Singles">
      <div className="flex flex-col gap-2 lg:flex-row lg:flex-wrap lg:gap-3">
        <Segmented label="Market kind" options={KINDS} value={q.kind} name={(k) => KIND_NAME[k]} testid="props-kind"
                   onChange={(kind) => go({ kind })} />
        <Segmented label="Tier" options={SINGLE_TIERS} value={q.tier as (typeof SINGLE_TIERS)[number]}
                   counts={page.counts} name={(t) => SINGLE_TIER_NAME[t]} testid="props-tier"
                   onChange={(tier) => go({ tier })} />
      </div>

      {/* Phone: the search on its own line, the sort under it (side by side it cut the placeholder off). */}
      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
        <form role="search" className="flex w-full min-w-0 items-center gap-2 rounded-pill border border-border bg-surface px-3 sm:max-w-sm sm:flex-1"
              onSubmit={(e) => { e.preventDefault(); go({ q: text.trim() || undefined }); }}>
          <MagnifyingGlass size={16} aria-hidden className="shrink-0 text-text-muted" />
          <input type="search" value={text} onChange={(e) => setText(e.target.value)} data-testid="props-search"
                 placeholder="Player, team or stat" aria-label="Search player, team or stat"
                 className="min-h-10 min-w-0 flex-1 bg-transparent text-base text-text placeholder:text-text-muted focus:outline-none" />
        </form>
        <div role="group" aria-label="Sort" className="flex gap-4">
          {SORTS.map((s) => (
            <button key={s.s} type="button" aria-pressed={q.sort === s.s} data-testid={`props-sort-${s.s}`} onClick={() => go({ sort: s.s })}
                    className={`-mb-px min-h-10 border-b-2 text-sm transition-colors ${
                      q.sort === s.s ? "border-accent-ink font-semibold text-text" : "border-transparent text-text-muted hover:text-text"}`}>
              {s.face}
            </button>
          ))}
        </div>
      </div>

      <p className="mt-4 text-sm text-text-muted">
        {q.tier === "low" ? (
          <>Long shots under 5¢. <Link href={singlesHref(q, { tier: "safer" })} className="underline">Back to the tiers</Link></>
        ) : (
          <>
            <span className="hidden sm:inline">{SINGLE_TIER_NAME[q.tier]}: each side priced {SINGLE_TIER_BAND[q.tier]}.{" "}</span>
            {(page.counts.low ?? 0) > 0 && (
              <Link href={singlesHref(q, { tier: "low" })} data-testid="props-low" className="underline">
                Long shots under 5¢ ({page.counts.low})
              </Link>
            )}
          </>
        )}
      </p>

      {page.rows.length === 0 ? (
        <p data-testid="props-none" className="mt-4 border-t border-border pt-4 text-base text-text">
          {q.q ? `Nothing matches “${q.q}” in this tier.` : "Nothing in this tier for games not yet started."}
        </p>
      ) : (
        <ul className="mt-2 border-t border-border">
          {page.rows.map((r) => <Row key={`${r.m}-${r.side}`} r={r} sport={sport} />)}
        </ul>
      )}

      {n > PAGE_SIZE && (
        <nav aria-label="Pages" className="mt-4 flex items-center justify-between gap-3 text-sm">
          {q.offset > 0 ? (
            <Link href={singlesHref(q, { offset: Math.max(q.offset - PAGE_SIZE, 0) })} data-testid="props-prev" className="tap inline-flex items-center text-text">‹ Previous</Link>
          ) : <span />}
          <span className="label-mono text-text-muted">{from}–{to} of {n}</span>
          {to < n ? (
            <Link href={singlesHref(q, { offset: q.offset + PAGE_SIZE })} data-testid="props-next" className="tap inline-flex items-center text-text">Next ›</Link>
          ) : <span />}
        </nav>
      )}
    </section>
  );
}
