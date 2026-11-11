"use client";

import { useCallback, useEffect, useMemo, useState, useSyncExternalStore, useTransition } from "react";

import { expandGame } from "@/app/(app)/betting/actions";
import { ComboSheet } from "@/components/betting/combo-sheet";
import {
  asOfLabel, cents, comboLabel, countsLine, edgeCents, edgeTone, feeCents, orderEvents, parseConsensus, pct,
  staleLabel, startLabel, tierLabel,
  type BoardEvent, type BoardGame, type BoardMarket, type Consensus, type Kind,
} from "@/lib/betting/board";
import {
  loadLegs, marketTicker, saveLegs, sideOf, toggleLeg, type ComboLeg, type Side,
} from "@/lib/betting/combo";

/** Combo mode (Chat 8 Phase 3): the ticked legs and how to tick one. */
type ComboCtx = { on: boolean; legs: readonly ComboLeg[]; tick: (leg: ComboLeg) => void };

// One sport's games, collapsed (owner, Nov 6). Expanding a game loads its
// markets through the expandGame action, once; collapsing keeps them. Each
// market: its label, YES bid / ask, the taker fee per contract at the ask, and
// the event's combo chip and source time. Phone-first: one column, the
// prices in a fixed mono column on the right.

function ComboChip({ ev }: { ev: BoardEvent }) {
  const cap = ev.sm !== undefined && ev.sm !== null;
  return (
    <span data-testid="combo-chip" data-yes-only={ev.y ? "true" : undefined} data-size-max={cap ? ev.sm : undefined}
          title={cap ? `A Kalshi combo may use at most ${ev.sm} of this event's markets` : undefined}
          className="label-mono inline-flex h-6 shrink-0 items-center whitespace-nowrap rounded-pill border border-accent px-2 text-accent">
      {comboLabel(ev)}
    </span>
  );
}

// A scalar market (db/072): YES pays a fraction of $1 that grows with the
// stat, so it is not a hit-or-miss leg and gets no combo chip.
function ScalesChip() {
  return (
    <span data-testid="scales-chip"
          title="Scalar: YES pays a share of $1 that grows with the stat. Not a parlay leg."
          className="label-mono ml-2 inline-flex h-6 items-center whitespace-nowrap rounded-pill border border-border px-2 align-middle text-text-muted">
      Scales
    </span>
  );
}

function TickButtons({ leg, ticked, tick }: {
  leg: Omit<ComboLeg, "side">; ticked: Side | null; tick: (leg: ComboLeg) => void;
}) {
  return (
    <span data-testid="combo-ticks" className="flex w-full basis-full items-center gap-1 pt-1">
      {(["yes", "no"] as const).map((side) => (
        <button key={side} type="button" data-testid={`tick-${side}`} aria-pressed={ticked === side}
                aria-label={`${side === "yes" ? "YES" : "NO"} on ${leg.label} in the combo`}
                onClick={() => tick({ ...leg, side })}
                className="tap group inline-flex items-center justify-center px-0.5">
          <span className={`label-mono inline-flex h-8 items-center rounded-pill border px-3 ${
            ticked === side ? "border-accent bg-accent text-on-accent" : "border-border text-text-muted group-hover:border-accent"}`}>
            {side === "yes" ? "YES" : "NO"}
          </span>
        </button>
      ))}
    </span>
  );
}

function MarketRow({ m, ev, game, combo }: { m: BoardMarket; ev: BoardEvent; game: BoardGame; combo: ComboCtx }) {
  const consensus = parseConsensus(m);
  const ticker = combo.on && !m.s ? marketTicker(ev, m) : null;
  return (
    <li data-testid="board-market" data-kind={m.k} data-payout={m.s ? "scalar" : "binary"}
        className="flex flex-wrap items-baseline justify-between gap-x-4 py-2">
      <span className="min-w-0 flex-1 text-sm text-text">
        {m.lb ?? "—"}
        {m.v && <span className="label-mono ml-2 text-text-muted">{m.v}</span>}
        {m.s && <ScalesChip />}
      </span>
      <span className="shrink-0 text-right font-mono text-sm tabular-nums text-text">
        <span data-testid="market-yes" aria-label={`YES bid ${cents(m.b)}, ask ${cents(m.a)}`}>
          {cents(m.b)}<span className="text-text-muted"> / </span>{cents(m.a)}
        </span>
        <span data-testid="market-fee" className="block text-xs text-text-muted">fee {feeCents(m.f)}</span>
      </span>
      {consensus && <ConsensusRow q={consensus} />}
      {ticker && (
        <TickButtons leg={{ t: ticker, label: m.lb ?? ticker, game: game.t }} ticked={sideOf(combo.legs, ticker)}
                     tick={combo.tick} />
      )}
    </li>
  );
}

// The consensus row (db/073, db/074): tier chip, the consensus probability,
// the better side's ask and fee, and its net edge. A claimed positive edge is
// in the up colour; anything else is neutral, never danger. A stale price is
// tagged and claims nothing.
function ConsensusRow({ q }: { q: Consensus }) {
  const side = q.sd === "n" ? "NO" : "YES";
  const stale = staleLabel(q);
  return (
    <span data-testid="market-consensus" data-tier={q.t} data-claimed={q.ec ? "1" : "0"} data-stale={stale ? "1" : "0"}
          className="flex w-full basis-full flex-wrap items-center gap-x-3 gap-y-1 pt-1 font-mono text-xs tabular-nums text-text-muted">
      <span data-testid="consensus-tier"
            className="label-mono inline-flex h-6 items-center whitespace-nowrap rounded-pill border border-border px-2">
        {tierLabel(q)}
      </span>
      <span data-testid="consensus-p">p {pct(q.p)}</span>
      <span data-testid="consensus-ask">{side} {cents(q.a)}</span>
      <span data-testid="consensus-fee">fee {q.fApprox ? cents(q.f) : feeCents(q.f)}</span>
      <span data-testid="consensus-edge" className={edgeTone(q) === "up" ? "text-hit" : "text-text-muted"}>
        edge {edgeCents(q.e)}
      </span>
      {stale && (
        <span data-testid="consensus-stale"
              className="label-mono inline-flex h-6 items-center whitespace-nowrap rounded-pill border border-border px-2">
          {stale}
        </span>
      )}
    </span>
  );
}

function EventBlock({ ev, game, combo }: { ev: BoardEvent; game: BoardGame; combo: ComboCtx }) {
  return (
    <section data-testid="board-event" data-event={ev.e} className="mt-4 first:mt-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="label-mono text-text-muted">{ev.sr}</h4>
        <span className="flex items-center gap-2">
          {ev.c && <ComboChip ev={ev} />}
          <span data-testid="event-asof" className="label-mono text-text-muted">{asOfLabel(ev.asof)}</span>
        </span>
      </div>
      <ul className="mt-1 divide-y divide-border border-t border-border">
        {ev.m.map((m, i) => <MarketRow key={i} m={m} ev={ev} game={game} combo={combo} />)}
      </ul>
    </section>
  );
}

function GameItem({ game, kind, combo, legs }: { game: BoardGame; kind: Kind | null; combo: boolean; legs: ComboCtx }) {
  const [open, setOpen] = useState(false);
  const [events, setEvents] = useState<BoardEvent[] | null>(null);
  // Whether the loaded markets carry tickers (db/077; Combo mode only).
  const [withTickers, setWithTickers] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const panel = `game-${game.g}`;

  const load = useCallback((tickers: boolean) => {
    start(async () => {
      const res = await expandGame(game.g, kind, combo, tickers);
      if (res.ok) {
        setEvents(orderEvents(res.events));
        setWithTickers(tickers);
        setError(null);
      } else {
        setError(res.error);
      }
    });
  }, [game.g, kind, combo]);

  // Combo mode switched on with this game open: load its tickers once.
  useEffect(() => {
    if (open && legs.on && events !== null && !withTickers && !pending) load(true);
  }, [open, legs.on, events, withTickers, pending, load]);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && events === null && !pending) load(legs.on);
  };

  return (
    <li data-testid="board-game" data-game={game.g} data-league={game.lg} className="border-b border-border">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-controls={panel}
        className="block w-full py-4 text-left transition-colors hover:bg-surface-2"
      >
        <p className="label-mono flex flex-wrap items-center gap-x-2 text-text-muted">
          {game.lg && <span>{game.lg.toUpperCase()} ·</span>}
          <span data-testid="game-start">{startLabel(game.at)}</span>
          {game.test && <span className="rounded-pill border border-border px-1.5">test</span>}
        </p>
        <p className="mt-1 text-base text-text">{game.t}</p>
        <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-text-muted">
          <span data-testid="game-counts">{countsLine(game)}</span>
          {game.c ? <span className="label-mono text-accent">{game.c} combo</span> : null}
          <span className="label-mono">{asOfLabel(game.asof)}</span>
        </p>
      </button>
      {open && (
        <div id={panel} data-testid="board-game-markets" aria-busy={pending} className="pb-4">
          {pending && <p className="text-sm text-text-muted">Loading markets…</p>}
          {error && <p role="status" className="text-sm text-danger">{error}</p>}
          {events && events.length === 0 && <p className="text-sm text-text-muted">No markets match these filters.</p>}
          {events?.map((ev) => <EventBlock key={ev.e} ev={ev} game={game} combo={legs} />)}
        </div>
      )}
    </li>
  );
}

// The ticks and the mode live in this browser (owner, Nov 11: ticks persist
// across games and sports), read through useSyncExternalStore so the server
// render (nothing ticked, mode off) matches. Blocked storage keeps them for
// this page only (the fallback below).
const MODE_KEY = "vm-combo-mode";
const listeners = new Set<() => void>();
let fallback: { legs: string | null; mode: string | null } = { legs: null, mode: null };

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function subscribe(fn: () => void) {
  listeners.add(fn);
  window.addEventListener("storage", fn);
  return () => {
    listeners.delete(fn);
    window.removeEventListener("storage", fn);
  };
}

function readRaw(key: string): string | null {
  try {
    return storage()?.getItem(key) ?? (key === MODE_KEY ? fallback.mode : fallback.legs);
  } catch {
    return key === MODE_KEY ? fallback.mode : fallback.legs;
  }
}

function notify() {
  for (const fn of listeners) fn();
}

const LEGS_KEY = "vm-combo-legs";

export function BoardView({ games, kind, combo }: { games: BoardGame[]; kind: Kind | null; combo: boolean }) {
  const rawLegs = useSyncExternalStore(subscribe, () => readRaw(LEGS_KEY), () => null);
  const on = useSyncExternalStore(subscribe, () => readRaw(MODE_KEY) === "1", () => false);
  const legs = useMemo(
    () => loadLegs({ getItem: () => rawLegs }),
    [rawLegs],
  );
  const update = useCallback((next: ComboLeg[]) => {
    fallback = { ...fallback, legs: next.length ? JSON.stringify(next) : null };
    saveLegs(storage(), next);
    notify();
  }, []);
  const ctx: ComboCtx = { on, legs, tick: (leg) => update(toggleLeg(legs, leg)) };
  const switchMode = () => {
    const next = on ? "0" : "1";
    fallback = { ...fallback, mode: next };
    try {
      storage()?.setItem(MODE_KEY, next);
    } catch {
      // storage blocked: the mode lasts this page (the fallback)
    }
    notify();
  };

  return (
    <>
      <p className="mb-3 flex flex-wrap items-center gap-3 lg:max-w-3xl">
        <button type="button" data-testid="combo-toggle" aria-pressed={on} onClick={switchMode}
                className="tap group inline-flex items-center justify-center">
          <span className={`label-mono inline-flex h-8 items-center rounded-pill border px-3 ${
            on ? "border-accent bg-accent text-on-accent" : "border-border text-text-muted group-hover:border-accent"}`}>
            Combo
          </span>
        </button>
        {on && <span className="text-sm text-text-muted">Tick YES or NO on any binary market, in any game.</span>}
      </p>
      {games.length === 0 ? (
        <p data-testid="board-no-games" className="border-t border-border pt-4 text-base text-text-muted lg:max-w-3xl">
          No games match these filters.
        </p>
      ) : (
        <ul data-testid="board-games" className="border-t border-border lg:max-w-3xl">
          {games.map((g) => <GameItem key={g.g} game={g} kind={kind} combo={combo} legs={ctx} />)}
        </ul>
      )}
      {legs.length > 0 && <div aria-hidden className="h-64" />}
      <ComboSheet legs={legs} onRemove={(t) => update(legs.filter((l) => l.t !== t))} onClear={() => update([])} />
    </>
  );
}
