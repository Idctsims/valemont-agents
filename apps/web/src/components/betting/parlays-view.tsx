"use client";

import { X } from "@phosphor-icons/react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { pollParlays } from "@/app/(app)/betting/parlays/actions";
import { TicketCard } from "@/components/betting/parlay-card";
import { TicketDetails } from "@/components/betting/parlay-details";
import {
  TIER_NAME, emptySlots, inferLeg, sortTickets, stage, type GameLive, type ParlayDay, type ParlayTicket,
} from "@/lib/betting/parlays";

// The Parlays tab (Chat 8 Phase 4.5; docs/parlays_page.md §3). One Chicago
// day: Live now, Starts later, Settled; a tier filter (a list, never a
// total); the empty slots behind one line; Details as a sheet on the phone
// and a panel on desktop. The live count polls ESPN through the server every
// 30 s while a ticket's game is on (§4). Paper: nothing here bets.

const POLL_MS = 30_000;
const FILTERS = ["all", "safer", "mid", "lotto"] as const;
type Filter = (typeof FILTERS)[number];

function dayLabel(day: string, today: string): string {
  if (day === today) return "Today";
  const d = new Date(`${day}T12:00:00Z`);
  return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
}

/** What the live poll asks for: each linked game with the players the tickets name. */
function requests(tickets: ParlayTicket[], games: ParlayDay["games"], only?: (t: ParlayTicket) => boolean) {
  const by = new Map<number, Set<string>>();
  for (const t of tickets) {
    if (only && !only(t)) continue;
    for (const l of t.legs) {
      if (!l.g) continue;
      const s = by.get(l.g) ?? new Set<string>();
      const x = inferLeg(l, games[l.g]);
      if (x.kind === "prop" && x.who) s.add(x.who);
      by.set(l.g, s);
    }
  }
  return [...by].map(([g, names]) => ({ g, names: [...names] }));
}

function EmptySlots({ day }: { day: ParlayDay }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const { rows, noMarket } = emptySlots(day.results);
  const total = day.results.length;
  if (total === 0) return null;
  const sports = [...new Set(rows.map((r) => r.sport ?? "every sport"))];
  const bySlot = (slot: string) => rows.filter((r) => r.slot === slot);
  return (
    <>
      <button type="button" data-testid="parlay-empties-open" onClick={() => dialog.current?.showModal()}
              className="mt-6 flex w-full flex-col items-start rounded-inner border border-dashed border-border px-4 py-3.5 text-left">
        <span className="font-semibold text-text">{total} empty slot{total === 1 ? "" : "s"}</span>
        <span className="text-sm text-text-muted">
          {sports.length ? `${sports.slice(0, 4).join(", ")}${sports.length > 4 ? "…" : ""} · see why` : "No eligible market · see why"}
        </span>
      </button>
      <dialog ref={dialog} data-testid="parlay-empties" aria-label="Empty slots"
              onClick={(e) => { if (e.target === dialog.current) dialog.current?.close(); }}
              className="m-0 mx-auto mt-4 w-full max-w-xl bg-transparent p-4 text-text backdrop:bg-bg/80 backdrop:backdrop-blur-sm sm:mt-16">
        <div className="relative max-h-tool overflow-y-auto rounded-inner border border-border bg-surface p-4">
          <button type="button" onClick={() => dialog.current?.close()} aria-label="Close empty slots"
                  className="tap absolute right-2 top-2 inline-flex size-10 items-center justify-center rounded-pill text-text-muted hover:text-text">
            <X size={20} aria-hidden />
          </button>
          <h2 className="font-display text-2xl text-text">Empty slots</h2>
          <p className="mt-1 max-w-measure text-sm text-text-muted">
            A slot with no ticket is recorded with the rule that bound it, never skipped.
          </p>
          {(["16:45", "10:45"] as const).map((slot) => {
            const list = bySlot(slot);
            const none = [...noMarket].filter(([k]) => k.startsWith(slot));
            if (!list.length && !none.length) return null;
            return (
              <section key={slot} className="mt-4">
                <h3 className="label-mono text-text-muted">{slot === "10:45" ? "10:45 AM" : "4:45 PM"} slot</h3>
                <ul className="mt-1">
                  {list.map((r, i) => (
                    <li key={i} data-testid="parlay-empty" className="border-b border-border py-2">
                      <p className="flex justify-between gap-3">
                        <span className="font-semibold text-text">{TIER_NAME[r.tier] ?? r.tier} · {r.scope === "cross_sport" ? "Cross-sport" : r.sport ?? "all sports"}</span>
                        <span className="text-sm text-text">{r.rule}</span>
                      </p>
                      {r.raw && r.raw !== r.rule && <p className="font-mono text-xs text-text-muted">{r.raw}</p>}
                    </li>
                  ))}
                  {none.map(([k, sp]) => (
                    <li key={k} className="border-b border-border py-2">
                      <p className="flex justify-between gap-3">
                        <span className="font-semibold text-text">{TIER_NAME[k.split(" ")[1]] ?? k}</span>
                        <span className="text-sm text-text">no eligible market</span>
                      </p>
                      <p className="text-xs text-text-muted">{sp.join(", ")}</p>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      </dialog>
    </>
  );
}

export function ParlaysView({ day, today, initialLive = {}, poll = true }: {
  day: ParlayDay;
  today: string;
  /** Live counts already read (none on the page: the client asks on open). */
  initialLive?: Record<string, GameLive>;
  /** Off only where there is no owner session to poll with. */
  poll?: boolean;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [live, setLive] = useState<Record<string, GameLive>>(initialLive);
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));
  const [open, setOpen] = useState<number | null>(null);
  const [liveError, setLiveError] = useState<string | null>(null);
  const sheet = useRef<HTMLDialogElement>(null);

  const tickets = useMemo(() => sortTickets(day.tickets), [day.tickets]);
  const shown = tickets.filter((t) => filter === "all" || t.tier === filter);
  const groups = {
    live: shown.filter((t) => stage(t, now) === "live"),
    pre: shown.filter((t) => stage(t, now) === "pre"),
    done: shown.filter((t) => stage(t, now) === "done"),
  };
  const anyLive = tickets.some((t) => stage(t, now) === "live");
  const [selected, setSelected] = useState<number | null>(() => tickets[0]?.id ?? null);
  // The side panel exists on desktop only (it would fetch Details for nothing on a phone).
  const [wide, setWide] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 64rem)");
    const on = () => setWide(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  const refresh = useCallback(async (onlyLive: boolean) => {
    const req = requests(tickets, day.games, onlyLive ? (t) => stage(t, Math.floor(Date.now() / 1000)) === "live" : undefined);
    if (!req.length) return;
    const res = await pollParlays(req);
    if (res.ok) {
      setLive((prev) => ({ ...prev, ...res.games }));
      setLiveError(null);
    } else setLiveError(res.error);
  }, [tickets, day.games]);

  // Once on open (photos and finals), then every 30 s while a ticket's game is on.
  useEffect(() => { if (poll) void refresh(false); }, [refresh, poll]);
  useEffect(() => {
    const tick = setInterval(() => setNow(Math.floor(Date.now() / 1000)), POLL_MS);
    return () => clearInterval(tick);
  }, []);
  useEffect(() => {
    if (!anyLive || !poll) return;
    const timer = setInterval(() => { if (document.visibilityState === "visible") void refresh(true); }, POLL_MS);
    return () => clearInterval(timer);
  }, [anyLive, refresh, poll]);

  const details = (id: number) => {
    setOpen(id);
    setSelected(id);
    if (!wide) sheet.current?.showModal();
  };
  const openTicket = tickets.find((t) => t.id === (open ?? selected));

  const section = (key: keyof typeof groups, title: string, dot = false) =>
    groups[key].length > 0 && (
      <section aria-label={title} data-testid={`parlays-${key}`} className="mt-6 first:mt-0">
        <h2 className="mb-3 flex items-center gap-2 label-mono text-text-muted">
          {dot && <span aria-hidden className="inline-block size-2 rounded-pill bg-live" />}{title}
        </h2>
        <div className="flex flex-col gap-3">
          {groups[key].map((t) => (
            <TicketCard key={t.id} t={t} games={day.games} live={live} now={now} onDetails={details}
                        selected={selected === t.id} />
          ))}
        </div>
      </section>
    );

  return (
    <section aria-labelledby="parlays-h" data-testid="parlays-page" data-day={day.day}>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="label-mono text-text-muted">Paper parlays · nothing here is bet</p>
          <h1 id="parlays-h" className="mt-3 font-display text-4xl text-text lg:text-display">Parlays</h1>
        </div>
        <nav aria-label="Day" className="flex items-center gap-1">
          {day.prev ? <Link href={`/betting/parlays?day=${day.prev}`} aria-label="Previous day" data-testid="parlays-prev"
                            className="tap inline-flex items-center justify-center rounded-pill text-xl text-text-muted hover:text-text">‹</Link>
            : <span className="tap inline-flex" />}
          <span data-testid="parlays-day" className="min-w-24 text-center font-semibold text-text">{dayLabel(day.day, today)}</span>
          {day.next ? <Link href={`/betting/parlays?day=${day.next}`} aria-label="Next day" data-testid="parlays-next"
                            className="tap inline-flex items-center justify-center rounded-pill text-xl text-text-muted hover:text-text">›</Link>
            : <span className="tap inline-flex" />}
        </nav>
      </div>

      <div role="group" aria-label="Tier" className="mt-5 flex gap-2 overflow-x-auto">
        {FILTERS.map((f) => (
          <button key={f} type="button" aria-pressed={filter === f} data-testid={`parlays-filter-${f}`} onClick={() => setFilter(f)}
                  className={`tap rounded-pill border px-4 text-sm transition-colors ${
                    filter === f ? "border-text bg-text text-bg" : "border-border text-text-muted hover:text-text"}`}>
            {f === "all" ? "All" : TIER_NAME[f]}
          </button>
        ))}
      </div>
      {liveError && <p role="status" className="mt-3 text-sm text-on-pace">{liveError}</p>}

      <div className="mt-5 lg:grid lg:grid-betting lg:items-start lg:gap-8">
        <div className="min-w-0">
          {shown.length === 0 && (
            <p data-testid="parlays-none" className="max-w-measure border-t border-border pt-4 text-lg text-text">
              {day.tickets.length === 0
                ? (day.enabled === false ? "The parlay agents are off." : `No ticket ${day.day === today ? "yet today" : "this day"}.`)
                : `No ${TIER_NAME[filter]} ticket this day.`}
            </p>
          )}
          {section("live", "Live now", true)}
          {section("pre", "Starts later")}
          {section("done", "Settled")}
          <EmptySlots day={day} />
        </div>
        <aside aria-label="Ticket details" className="hidden lg:sticky lg:top-4 lg:block">
          {wide && openTicket && (
            <div className="rounded-card border border-border bg-surface p-5">
              <TicketDetails id={openTicket.id} started={stage(openTicket, now) !== "pre"} />
            </div>
          )}
        </aside>
      </div>

      <dialog ref={sheet} aria-label="Ticket details" data-testid="parlay-sheet"
              onClick={(e) => { if (e.target === sheet.current) sheet.current?.close(); }}
              onClose={() => setOpen(null)}
              className="m-0 mt-auto w-full max-w-none bg-transparent p-0 text-text backdrop:bg-bg/80 backdrop:backdrop-blur-sm lg:hidden">
        <div className="relative max-h-tool overflow-y-auto rounded-t-card border border-border bg-surface px-5 pb-8 pt-3">
          <span aria-hidden className="mx-auto mb-3 block h-1.5 w-10 rounded-pill bg-surface-3" />
          <button type="button" onClick={() => sheet.current?.close()} aria-label="Close details"
                  className="tap absolute right-2 top-2 inline-flex size-10 items-center justify-center rounded-pill text-text-muted hover:text-text">
            <X size={20} aria-hidden />
          </button>
          {open !== null && openTicket && <TicketDetails id={open} started={stage(openTicket, now) !== "pre"} />}
        </div>
      </dialog>
    </section>
  );
}
