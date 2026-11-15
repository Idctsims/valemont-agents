"use client";

import Link from "next/link";
import { useCallback, useMemo, useState, useTransition } from "react";

import { loadTab } from "@/app/(app)/betting/actions";
import { useBetting } from "@/components/betting/betting-store";
import { asOfLabel, comboLabel, edgeCents, parseConsensus, pct, startLabel } from "@/lib/betting/board";
import { sideOf } from "@/lib/betting/combo";
import {
  OTHER_STAT, TABS, TAB_LABEL, boxTitle, byLine, byTeam, isLadder, isWinnerEvent, latestAsOf, mainIndex,
  marketLabel, marketTicker, orderBoxes, periodLabel, periodsOf, playerRows, rungLabel, rungWindow, sidesOf,
  singleLine, statLabel, tabKey, winnerConsensus,
  type GameMeta, type GameTabs, type Tab, type TabEvent, type TabMarket,
} from "@/lib/betting/game";
import { priceText } from "@/lib/betting/price";

// A game's own page (Workstream B, the approved mock): its header with the
// moneyline and the consensus bar, then three tabs: Game lines, Players,
// Halves and quarters. Each tab (and each Players stat) loads once through
// loadTab (db/079) and is cached for the life of the page; Refresh re-reads
// the one in view and shows its source time. Tapping a price puts its YES
// side in the slip (before fee; the slip totals after fee). Only a claimed
// edge (books tier, fresh prices, db/078) uses the up colour.

type Loaded = { events: TabEvent[]; at: number };

type Ctx = { meta: GameMeta; names: Map<string, string>; tab: Tab };

function nameOf(ctx: Ctx, code: string | undefined): string {
  if (!code) return "";
  return ctx.names.get(code) ?? code;
}

/** A leg's words in the slip: the player or side, the rung, the stat or market, and the period. */
function legLabel(ctx: Ctx, ev: TabEvent, m: TabMarket): string {
  const per = ev.pd ? `, ${periodLabel(ev.pd)}` : "";
  if (isWinnerEvent(ev)) return `${m.sj ?? marketLabel(m)} to win${per}`;
  if (ctx.tab === "players" && m.sj && typeof m.ln === "number") {
    return `${m.sj} ${rungLabel(m, "players")} ${m.st ? statLabel(m.st).toLowerCase() : ""}`.trim()
      + (m.v ? ` (${m.v})` : "");
  }
  return `${marketLabel(m)}${per}`;
}

function claimedYes(m: TabMarket) {
  const q = parseConsensus(m);
  return !!q && q.ec && q.e > 0 && q.sd === "y";
}

function Odd({ ctx, ev, m, face, big = false, row = false }: {
  ctx: Ctx; ev: TabEvent; m: TabMarket; face: string; big?: boolean; row?: boolean;
}) {
  const { unit, legs, tick } = useBetting();
  const ticker = marketTicker(ev, m);
  const label = legLabel(ctx, ev, m);
  if (m.s) {
    return (
      <span data-testid="board-market" data-payout="scalar" title="Scalar: YES pays a share of $1 that grows with the stat. Not a parlay leg."
            className="flex min-h-12 flex-col items-center justify-center gap-1 rounded-tile border border-dashed border-border px-2 py-1 text-center">
        <span className="text-xs text-text-muted">{face}</span>
        <span data-testid="scales-chip" className="label-mono text-text-muted">Scales</span>
      </span>
    );
  }
  const on = sideOf(legs, ticker) === "yes";
  const up = claimedYes(m);
  return (
    <button type="button" data-testid={big ? "ml-pick" : "board-market"} data-payout="binary" data-ticker={ticker}
            data-claimed={up ? "1" : "0"} aria-pressed={on}
            aria-label={`${label}: ${priceText(m.a, unit)}${on ? ", in the slip" : ""}`}
            onClick={() => tick({ t: ticker, side: "yes", label, game: ctx.meta.title, sport: ctx.meta.sport ?? undefined,
                                  a: typeof m.a === "number" ? m.a : undefined, tm: m.tm })}
            className={`flex min-w-0 rounded-tile border transition-colors ${
              row ? "min-h-12 items-center justify-between gap-2 px-3 py-1.5" : "min-h-12 flex-col items-center justify-center px-1.5 py-1"} ${
              big ? "min-h-16 w-full" : ""} ${
              on ? "border-accent bg-accent text-on-accent" : "border-border bg-bg text-text hover:border-accent"}`}>
      <span data-testid="market-label"
            className={`${row ? "truncate text-left text-sm" : "text-center text-xs"} ${on ? "" : "text-text-muted"} ${big ? "label-mono" : ""}`}>
        {face}
        {m.v && <span className="block label-mono">{m.v}</span>}
      </span>
      <span data-testid="market-price"
            className={`font-mono tabular-nums ${big ? "text-2xl" : "text-sm"} ${on ? "" : up ? "text-hit" : ""}`}>
        {priceText(m.a, unit)}
      </span>
    </button>
  );
}

/** The line under a box's title: whose line is the main one, or that the prices are Kalshi's own. */
function SayLine({ ctx, main, count }: { ctx: Ctx; main: TabMarket | null; count?: string }) {
  const q = main ? parseConsensus(main) : null;
  const say = q && q.t !== "k"
    ? <span data-testid="box-consensus" data-tier={q.t}>
        <span className="font-mono text-text">{rungLabel(main!, ctx.tab)}</span> is the {q.t === "b" ? "books'" : "ESPN book's"} line
        {" · "}{q.t === "b" ? `${q.n ?? 0} books say` : "it says"} {pct(q.p)}
      </span>
    : <span data-testid="box-consensus" data-tier="k">No sportsbook line here. Kalshi&apos;s own prices.</span>;
  return (
    <p className="flex flex-wrap justify-between gap-2 text-xs text-text-muted">
      {say}
      {count && <span>{count}</span>}
    </p>
  );
}

/** Claimed edges in a box, in words; the up colour, and only for a claim (db/078). */
function Claims({ ctx, ms }: { ctx: Ctx; ms: readonly TabMarket[] }) {
  const claims = ms.flatMap((m) => {
    const q = parseConsensus(m);
    return q && q.ec && q.e > 0 ? [{ m, q }] : [];
  });
  if (claims.length === 0) return null;
  return (
    <ul data-testid="box-claims" className="flex flex-col gap-1 text-xs">
      {claims.map(({ m, q }) => (
        <li key={m.x} className="text-hit">
          Claimed edge {edgeCents(q.e)} on {q.sd === "n" ? "NO" : "YES"}: {rungLabel(m, ctx.tab)}
          {q.sd === "n" && <span className="text-text-muted"> (the slip takes YES; NO is not tappable yet)</span>}
        </li>
      ))}
    </ul>
  );
}

function Segment<T extends string>({ label, options, value, onPick, testId }: {
  label: string; options: { v: T; face: string }[]; value: T; onPick: (v: T) => void; testId: string;
}) {
  return (
    <div role="group" aria-label={label} data-testid={testId}
         className="inline-flex max-w-full overflow-x-auto rounded-inner border border-border p-0.5">
      {options.map((o) => (
        <button key={o.v} type="button" aria-pressed={value === o.v} onClick={() => onPick(o.v)}
                className={`tap shrink-0 whitespace-nowrap rounded-inner px-3 text-xs transition-colors ${
                  value === o.v ? "bg-accent text-on-accent" : "text-text-muted hover:text-text"}`}>
          {o.face}
        </button>
      ))}
    </div>
  );
}

function Ladder({ ctx, ev, ms, keyId }: { ctx: Ctx; ev: TabEvent; ms: TabMarket[]; keyId: string }) {
  const [all, setAll] = useState(false);
  const rows = byLine(ms);
  const main = mainIndex(rows);
  const [lo, hi] = all ? [0, rows.length] : rungWindow(rows.length, main);
  const mainIsBooks = /^[be]/.test(rows[main]?.q ?? "");
  return (
    <div className="flex flex-col gap-3">
      <SayLine ctx={ctx} main={mainIsBooks ? rows[main] : null} count={`${rows.length} lines`} />
      <div className="grid grid-rungs gap-1.5">
        {rows.slice(lo, hi).map((m) => (
          <div key={m.x} className={rows.indexOf(m) === main && mainIsBooks ? "rounded-tile ring-2 ring-accent" : ""}>
            <Odd ctx={ctx} ev={ev} m={m} face={rungLabel(m, ctx.tab)} />
          </div>
        ))}
      </div>
      {rows.length > hi - lo && !all && (
        <button type="button" data-testid="ladder-all" onClick={() => setAll(true)} aria-controls={keyId}
                className="tap self-start text-sm text-accent">All {rows.length} lines</button>
      )}
      {all && rows.length > 10 && (
        <button type="button" onClick={() => setAll(false)} className="tap self-start text-sm text-accent">Show fewer</button>
      )}
    </div>
  );
}

function MarketBox({ ctx, ev, title }: { ctx: Ctx; ev: TabEvent; title: string }) {
  const groups = byTeam(ev.m);
  const teams = [...groups.keys()];
  const ladder = isLadder(ev.m);
  const [team, setTeam] = useState(() => {
    if (!ladder || teams.length < 2) return teams[0] ?? "";
    const rows = ev.m;
    return rows[mainIndex(rows)]?.tm ?? teams[0];
  });
  const id = `box-${ev.e}`;
  return (
    <section data-testid="board-event" data-event={ev.e} id={id}
             className="overflow-hidden rounded-inner border border-border bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
        <h3 className="font-display text-xl text-text">{title}</h3>
        <span className="flex flex-wrap items-center gap-2">
          {ev.c && (
            <span data-testid="combo-chip" data-yes-only={ev.y ? "true" : undefined}
                  data-size-max={ev.sm ?? undefined}
                  title={ev.sm !== undefined ? `A Kalshi combo may use at most ${ev.sm} of this event's markets` : undefined}
                  className="label-mono inline-flex h-6 items-center whitespace-nowrap rounded-pill border border-accent px-2 text-accent">
              {comboLabel(ev)}
            </span>
          )}
          {ladder && teams.length > 1 && (
            <Segment label="Team" testId="box-team" value={team} onPick={setTeam}
                     options={teams.map((t) => ({ v: t, face: t ? nameOf(ctx, t) : "Other" }))} />
          )}
        </span>
      </div>
      <div className="flex flex-col gap-3 px-4 py-4">
        {ladder ? (
          <Ladder key={team} ctx={ctx} ev={ev} ms={teams.length > 1 ? (groups.get(team) ?? []) : ev.m} keyId={id} />
        ) : (
          <>
            <SayLine ctx={ctx} main={ev.m.find((m) => /^[be]/.test(m.q ?? "")) ?? null} />
            <div className="grid grid-tiles gap-1.5">
              {[...ev.m].sort((x, y) => (x.tm ?? "").localeCompare(y.tm ?? "")).map((m) => (
                <Odd key={m.x} ctx={ctx} ev={ev} m={m} face={marketLabel(m)} row />
              ))}
            </div>
          </>
        )}
        <Claims ctx={ctx} ms={ev.m} />
      </div>
    </section>
  );
}

function Boxes({ ctx, events }: { ctx: Ctx; events: TabEvent[] }) {
  const ordered = orderBoxes(events);
  const series = new Map<string, number>();
  for (const e of ordered) series.set(e.sr, (series.get(e.sr) ?? 0) + 1);
  if (ordered.length === 0) return <p className="rounded-inner border border-border bg-surface px-4 py-5 text-sm text-text-muted">No open markets here.</p>;
  return (
    <div className="flex flex-col gap-3">
      {ordered.map((ev) => (
        <MarketBox key={ev.e} ctx={ctx} ev={ev} title={boxTitle(ev, ctx.meta.g, (series.get(ev.sr) ?? 0) > 1)} />
      ))}
    </div>
  );
}

function Players({ ctx, events, stat }: { ctx: Ctx; events: TabEvent[]; stat: string }) {
  const [side, setSide] = useState("all");
  const byTeamRows = playerRows(events);
  const teams = [...byTeamRows.keys()];
  const shown = side === "all" ? teams : teams.filter((t) => t === side);
  if (events.length === 0) return <p className="rounded-inner border border-border bg-surface px-4 py-5 text-sm text-text-muted">No open markets for this stat.</p>;
  return (
    <div className="flex flex-col gap-3">
      {teams.length > 1 && (
        <Segment label="Team" testId="players-team" value={side} onPick={setSide}
                 options={[{ v: "all", face: "Both teams" }, ...teams.map((t) => ({ v: t, face: t ? nameOf(ctx, t) : "Other" }))]} />
      )}
      {shown.map((t) => {
        const rows = byTeamRows.get(t) ?? [];
        const tiles = singleLine(rows);
        return (
          <section key={t || "none"} data-testid="players-team-box" data-team={t}
                   className="overflow-hidden rounded-inner border border-border bg-surface">
            <p className="flex items-center gap-2 border-b border-border bg-surface-2 px-4 py-2.5 text-sm text-text">
              {t && <span className="font-mono">{t}</span>}
              <span>{t ? nameOf(ctx, t) : "No team"}</span>
              <span className="label-mono ml-auto text-text-muted">{statLabel(stat)} · {rows.length} {rows.length === 1 ? "player" : "players"}</span>
            </p>
            {tiles ? (
              <div className="grid grid-tiles gap-1.5 p-4">
                {rows.map((r) => <Odd key={r.name} ctx={ctx} ev={r.markets[0].ev} m={r.markets[0]} face={r.name} row />)}
              </div>
            ) : (
              <ul className="divide-y divide-border">
                {rows.map((r) => (
                  <li key={r.name} data-testid="player-row" className="grid gap-2 px-4 py-2.5 sm:grid-player sm:items-center">
                    <span className="truncate text-sm text-text">{r.name}</span>
                    <span className="grid grid-rungs gap-1.5">
                      {r.markets.map((m) => <Odd key={`${m.ev.e}-${m.x}`} ctx={ctx} ev={m.ev} m={m} face={rungLabel(m, "players")} />)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}

function Chips({ label, testId, items, value, onPick }: {
  label: string; testId: string; items: { v: string; face: string; n?: number }[]; value: string; onPick: (v: string) => void;
}) {
  return (
    <div role="group" aria-label={label} data-testid={testId} className="-mx-1 flex gap-1 overflow-x-auto pb-1">
      {items.map((it) => (
        <button key={it.v} type="button" data-testid={`${testId}-chip`} data-value={it.v} aria-pressed={value === it.v}
                onClick={() => onPick(it.v)} className="tap group inline-flex shrink-0 items-center justify-center px-1">
          <span className={`inline-flex h-9 items-center whitespace-nowrap rounded-pill border px-3 text-xs transition-colors ${
            value === it.v ? "border-text bg-text text-bg" : "border-border bg-surface text-text-muted group-hover:text-text"}`}>
            {it.face}
            {it.n !== undefined && <span className="ml-1.5 font-mono opacity-70">{it.n}</span>}
          </span>
        </button>
      ))}
    </div>
  );
}

function Scoreboard({ ctx, ev }: { ctx: Ctx; ev: TabEvent | undefined }) {
  const sides = sidesOf(ctx.meta.title);
  if (!ev) {
    return <p className="px-4 pb-5 text-sm text-text-muted">No moneyline open for this game.</p>;
  }
  // Away first, as the title reads; markets whose team the title names go in its order.
  const order = sides ? [sides.a.code, sides.h.code] : [];
  const ms = [...ev.m].sort((x, y) => {
    const ix = order.indexOf(x.tm ?? ""), iy = order.indexOf(y.tm ?? "");
    return (ix < 0 ? 9 : ix) - (iy < 0 ? 9 : iy);
  });
  const cons = winnerConsensus({ ...ev, m: ms });
  const fills = ["bg-accent", "bg-surface-3", "bg-wood"];
  return (
    <>
      <div className="grid grid-matchup items-end gap-3 px-4 pb-4 pt-2">
        {ms.slice(0, 2).map((m, i) => (
          <div key={m.x} className={`flex min-w-0 flex-col items-center gap-1 text-center ${i === 1 ? "col-start-3" : ""}`}>
            <span className="font-display text-3xl text-text">{m.tm ?? m.sj}</span>
            <span className="max-w-full truncate text-xs text-text-muted">{nameOf(ctx, m.tm) !== m.tm ? nameOf(ctx, m.tm) : (m.sj ?? "")}</span>
            <Odd ctx={ctx} ev={ev} m={m} face="to win" big />
          </div>
        ))}
        <span aria-hidden className="col-start-2 row-start-1 self-center pb-8 text-xs text-text-muted">at</span>
      </div>
      {ms.length > 2 && (
        <div className="grid grid-tiles gap-1.5 px-4 pb-4">
          {ms.slice(2).map((m) => <Odd key={m.x} ctx={ctx} ev={ev} m={m} face={m.sj ?? marketLabel(m)} row />)}
        </div>
      )}
      {cons.p.some((p) => p !== null) && (
        <div data-testid="game-consensus" role="img" className="px-4 pb-4"
             aria-label={`Consensus: ${ms.map((m, i) => `${m.tm ?? m.sj} ${pct(cons.p[i])}`).join(", ")}`}>
          <p className="mb-1.5 flex items-baseline justify-between text-xs text-text-muted">
            <span className="font-mono text-sm text-text">{pct(cons.p[0])}</span>
            <span>{cons.source}</span>
            <span className="font-mono text-sm text-text">{pct(cons.p[1])}</span>
          </p>
          <div className="flex h-2 gap-0.5 overflow-hidden rounded-pill">
            {ms.map((m, i) => (
              <i key={m.x} className={`block h-full grow-share ${fills[i % fills.length]}`}
                 style={{ "--vm-share": cons.p[i] ?? 0 } as React.CSSProperties} />
            ))}
          </div>
        </div>
      )}
      <div className="px-4 pb-4 empty:hidden"><Claims ctx={ctx} ms={ms} /></div>
    </>
  );
}

export function GameView({ meta, tabs, initial }: { meta: GameMeta; tabs: GameTabs; initial: TabEvent[] }) {
  const sides = useMemo(() => sidesOf(meta.title), [meta.title]);
  const names = useMemo(() => {
    const m = new Map<string, string>();
    if (sides) for (const s of [sides.a, sides.h]) m.set(s.code, s.name);
    return m;
  }, [sides]);
  const [tab, setTab] = useState<Tab>("lines");
  const firstStat = tabs.stats[0]?.st ?? (tabs.other > 0 ? OTHER_STAT : null);
  const [stat, setStat] = useState<string | null>(firstStat);
  const [period, setPeriod] = useState<string | null>(null);
  const [cache, setCache] = useState<Record<string, Loaded>>(() => ({ lines: { events: initial, at: Date.now() } }));
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const [loadingKey, setLoadingKey] = useState<string | null>(null);

  const load = useCallback((t: Tab, s: string | null, force = false) => {
    const key = tabKey(t, s);
    if (!force && cache[key]) return;
    if (t === "players" && s === null) return;
    setLoadingKey(key);
    start(async () => {
      const res = await loadTab(meta.g, t, s);
      if (res.ok) {
        setCache((c) => ({ ...c, [key]: { events: res.events, at: Date.now() } }));
        setError(null);
      } else {
        setError(res.error);
      }
      setLoadingKey(null);
    });
  }, [cache, meta.g]);

  const pickTab = (t: Tab) => {
    setTab(t);
    setError(null);
    load(t, t === "players" ? stat : null);
  };
  const pickStat = (s: string) => {
    setStat(s);
    setError(null);
    load("players", s);
  };

  const key = tabKey(tab, tab === "players" ? stat : null);
  const loaded = cache[key];
  const lines = cache.lines?.events ?? [];
  const winner = lines.find(isWinnerEvent);
  const ctx: Ctx = { meta, names, tab };
  const count = { lines: tabs.lines, players: tabs.players, periods: tabs.periods };
  const statItems = [
    ...tabs.stats.map((s) => ({ v: s.st, face: statLabel(s.st), n: s.n })),
    ...(tabs.other > 0 ? [{ v: OTHER_STAT, face: "Other", n: tabs.other }] : []),
  ];
  const periods = loaded && tab === "periods" ? periodsOf(loaded.events) : [];
  const shownPeriod = period && periods.includes(period) ? period : periods[0] ?? null;
  const asof = loaded ? latestAsOf(loaded.events) : null;
  const busy = pending && loadingKey === key;

  return (
    <div data-testid="game-page" data-game={meta.g}>
      <Link href={meta.sport ? `/betting?sport=${encodeURIComponent(meta.sport)}` : "/betting"} data-testid="game-back"
            className="tap -ml-1 inline-flex items-center gap-1.5 px-1 text-sm text-accent">
        <span aria-hidden>←</span> {meta.sport ? `${meta.sport} board` : "Board"}
      </Link>

      <article className="mt-2 overflow-hidden rounded-inner border border-border bg-surface">
        <p className="flex justify-between gap-3 border-b border-border px-4 py-2 text-xs text-text-muted">
          <span>
            {meta.startsAt && <span data-testid="game-start" className="mr-2 text-text">{startLabel(meta.startsAt)}</span>}
            {meta.test && <span className="label-mono rounded-pill border border-border px-1.5">test</span>}
          </span>
          <span className="label-mono">{(meta.league ?? meta.sport ?? "").toUpperCase()}</span>
        </p>
        <h1 className="px-4 pb-1 pt-4 text-center font-display text-2xl text-text text-balance">{meta.title}</h1>
        <Scoreboard ctx={ctx} ev={winner} />
      </article>

      <nav aria-label="Markets" className="sticky top-14 z-20 -mx-4 mt-4 bg-bg px-4 pt-1 lg:top-0 lg:mx-0 lg:px-0">
        <div className="flex gap-6 overflow-x-auto border-b border-border">
          {TABS.map((t) => (
            <button key={t} type="button" data-testid={`game-tab-${t}`} aria-pressed={tab === t} onClick={() => pickTab(t)}
                    className={`tap -mb-px shrink-0 whitespace-nowrap border-b-2 pb-2 text-sm transition-colors ${
                      tab === t ? "border-accent text-text" : "border-transparent text-text-muted hover:text-text"}`}>
              {TAB_LABEL[t]}<span className="ml-1.5 font-mono text-xs text-text-muted">{count[t]}</span>
            </button>
          ))}
        </div>
      </nav>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <span data-testid="tab-asof" className="label-mono text-text-muted">
          {asof ? `Prices ${asOfLabel(asof)} · before fee` : busy ? "Loading…" : ""}
        </span>
        <button type="button" data-testid="tab-refresh" disabled={busy} onClick={() => load(tab, tab === "players" ? stat : null, true)}
                className="tap inline-flex items-center rounded-pill border border-border px-3 text-sm text-text hover:border-accent disabled:opacity-50">
          {busy ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {tab === "players" && statItems.length > 0 && stat !== null && (
        <div className="mt-2"><Chips label="Stat" testId="stat" items={statItems} value={stat} onPick={pickStat} /></div>
      )}
      {tab === "periods" && periods.length > 1 && shownPeriod && (
        <div className="mt-2">
          <Chips label="Period" testId="period" value={shownPeriod} onPick={setPeriod}
                 items={periods.map((p) => ({ v: p, face: periodLabel(p) }))} />
        </div>
      )}

      <div data-testid="tab-panel" data-tab={tab} aria-busy={busy} className="mt-3">
        {error && <p role="status" className="mb-3 text-sm text-danger">{error}</p>}
        {!loaded && busy && <p className="text-sm text-text-muted">Loading markets…</p>}
        {tab === "players" && stat === null && (
          <p className="rounded-inner border border-border bg-surface px-4 py-5 text-sm text-text-muted">No player markets are open for this game yet.</p>
        )}
        {loaded && tab === "lines" && <Boxes ctx={ctx} events={loaded.events.filter((e) => !isWinnerEvent(e))} />}
        {loaded && tab === "players" && stat !== null && <Players key={stat} ctx={ctx} events={loaded.events} stat={stat} />}
        {loaded && tab === "periods" && (
          periods.length === 0
            ? <p className="rounded-inner border border-border bg-surface px-4 py-5 text-sm text-text-muted">No half or quarter markets are open for this game yet.</p>
            : <Boxes ctx={ctx} events={loaded.events.filter((e) => e.pd === shownPeriod)} />
        )}
      </div>
    </div>
  );
}
