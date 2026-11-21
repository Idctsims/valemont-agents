"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { pollLive } from "@/app/(app)/gameday/actions";
import { GameRow, LiveMark } from "@/components/gameday/game-row";
import {
  POLL_SECONDS, applyPatches, dateLabel, dayLabel, pollIds, sectionize, type SlateRow,
} from "@/lib/gameday/slate";

// Live now · Tonight · This week, from the server's rows. The live poll
// (owner, 2026-11-04): every POLL_SECONDS while the tab is visible AND a row
// on screen is in play (or due to start), asking for those ids only; nothing
// otherwise, and never a Realtime subscription on the table. A hidden tab
// polls once on its return.

export function SlateView({ initial, today, all }: { initial: SlateRow[]; today: string; all: boolean }) {
  const [rows, setRows] = useState(initial);
  const [polls, setPolls] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const rowsRef = useRef(rows);
  useEffect(() => {
    rowsRef.current = rows;
  }, [rows]);

  const sections = useMemo(() => sectionize(rows, today), [rows, today]);
  // Whether anything on screen is live or due: decides if the poll runs at all.
  // eslint-disable-next-line react-hooks/purity
  const hot = useMemo(() => pollIds(rows, Date.now()).length, [rows]);

  const poll = useCallback(async () => {
    if (document.visibilityState !== "visible") return;
    const ids = pollIds(rowsRef.current, Date.now());
    if (ids.length === 0) return;
    const res = await pollLive(ids);
    if (res.ok) {
      setRows((cur) => applyPatches(cur, res.rows));
      setError(null);
    } else {
      setError(res.error);
    }
    setPolls((n) => n + 1);
  }, []);

  useEffect(() => {
    if (hot === 0) return;
    const id = window.setInterval(poll, POLL_SECONDS * 1000);
    const onVisible = () => {
      if (document.visibilityState === "visible") void poll();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [hot, poll]);

  const weekN = useMemo(() => sections.week.reduce((n, d) => n + d.rows.length, 0), [sections]);
  const jumps = [
    ...(sections.live.length > 0 ? [{ id: "live", label: "Live", n: sections.live.length }] : []),
    { id: "tonight", label: "Tonight", n: sections.tonight.length },
    { id: "week", label: "This week", n: weekN },
  ];
  const active = useSpy(jumps.map((j) => j.id));

  return (
    <div data-testid="gameday-slate" data-polling={hot > 0 ? "true" : "false"} data-polls={polls}>
      {/* The section bar: sticks under the top bar; each chip jumps to its section, the one in view lit. */}
      <nav aria-label="Sections" data-testid="gameday-jumps"
           className="sticky top-below-header z-20 -mx-4 mb-6 flex gap-2 overflow-x-auto border-b border-border bg-band px-4 py-3 lg:top-0 lg:-mx-6 lg:px-6">
        {jumps.map((j) => {
          const on = active === j.id;
          const isLiveChip = j.id === "live";
          return (
            <a key={j.id} href={`#${j.id}`} data-testid={`jump-${j.id}`} aria-current={on ? "true" : undefined}
               className={`inline-flex min-h-9 shrink-0 items-center gap-2 whitespace-nowrap rounded-pill border px-3.5 text-sm font-semibold transition-colors ${
                 isLiveChip
                   ? on ? "border-live bg-live text-bg" : "border-live bg-live-fill text-live"
                   : on ? "border-text bg-text text-bg" : "border-border bg-surface text-text hover:border-accent-ink"}`}>
              {isLiveChip && !on ? <LiveMark label="Live" /> : <span>{j.label}</span>}
              <span className={`font-mono text-xs tabular-nums ${on ? "opacity-80" : "text-text-muted"}`}>{j.n}</span>
            </a>
          );
        })}
      </nav>

      {error && (
        <p role="status" className="mb-4 text-sm text-danger">{error} It will try again in a minute.</p>
      )}

      <div className="flex flex-col gap-10">
        {sections.live.length > 0 && (
          <section id="live" aria-label="Live now" data-testid="section-live" className="scroll-mt-gameday">
            <Heading title="Live now" n={sections.live.length} live />
            {/* On the phone the live games swipe sideways, the next one peeking; a grid from sm. */}
            <ul className="-mx-4 flex snap-x snap-mandatory scroll-px-4 gap-3 overflow-x-auto px-4 pb-1 pt-3 sm:mx-0 sm:grid sm:grid-cols-2 sm:overflow-visible sm:px-0">
              {sections.live.map((r) => (
                <GameRow key={r.id} row={r}
                         className={`shrink-0 snap-start sm:w-auto ${sections.live.length > 1 ? "w-5/6" : "w-full"}`} />
              ))}
            </ul>
          </section>
        )}

        <section id="tonight" aria-label="Tonight" data-testid="section-tonight" className="scroll-mt-gameday">
          <Heading title="Tonight" note={dateLabel(today)} n={sections.tonight.length} />
          {sections.tonight.length > 0 ? (
            <Cards rows={sections.tonight} />
          ) : (
            <p data-testid="tonight-empty" className="pt-4 text-base text-text-muted">
              {sections.live.length > 0
                ? "Nothing else on tonight."
                : all ? "Nothing on tonight." : "Nothing on tonight across the five. All sports shows the rest."}
            </p>
          )}
        </section>

        <section id="week" aria-label="This week" data-testid="section-week" className="flex scroll-mt-gameday flex-col gap-6">
          <div>
            <h2 className="font-display text-3xl text-text">This week</h2>
            {all && <p className="mt-1 label-mono text-text-muted">The five only</p>}
          </div>
          {sections.week.length > 0 ? (
            sections.week.map(({ day, rows: dayRows }) => (
              <div key={day} data-testid="week-day" data-day={day}>
                <Band title={dayLabel(day, today)} n={dayRows.length} />
                <Cards rows={dayRows} />
              </div>
            ))
          ) : (
            <p data-testid="week-empty" className="text-base text-text-muted">
              Nothing scheduled across the five in the next 7 days.
            </p>
          )}
        </section>
      </div>
    </div>
  );
}

/** Which section is in view: the last one whose top has passed under the bar. */
function useSpy(ids: string[]): string | null {
  const [active, setActive] = useState<string | null>(null);
  const key = ids.join(",");
  useEffect(() => {
    const els = key.split(",").map((id) => document.getElementById(id)).filter((e): e is HTMLElement => !!e);
    if (els.length === 0) return;
    const pick = () => {
      const line = 160;
      let cur = els[0].id;
      for (const el of els) if (el.getBoundingClientRect().top <= line) cur = el.id;
      setActive(cur);
    };
    pick();
    window.addEventListener("scroll", pick, { passive: true });
    return () => window.removeEventListener("scroll", pick);
  }, [key]);
  return active;
}

/** A section's own heading: big, with its count; Live now in the live colour. */
function Heading({ title, note, n, live = false }: { title: string; note?: string; n: number; live?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <h2 className={`font-display text-3xl ${live ? "text-live" : "text-text"}`}>
        {title}{note && <span className="label-mono ml-3 align-middle text-text-muted">{note}</span>}
      </h2>
      <span className="label-mono text-text-muted">{n} {n === 1 ? "event" : "events"}</span>
    </div>
  );
}

/** The betting board's green day band: the day, and how many. */
function Band({ title, n }: { title: string; n: number }) {
  return (
    <div className="flex items-center justify-between rounded-t-tile bg-accent px-4 py-2.75 text-on-accent">
      <h3 className="text-sm font-semibold">{title}</h3>
      <span className="label-mono opacity-85">{n} {n === 1 ? "event" : "events"}</span>
    </div>
  );
}

function Cards({ rows }: { rows: SlateRow[] }) {
  return (
    <ul className="grid gap-3 pt-3 sm:grid-cols-2">
      {rows.map((r) => <GameRow key={r.id} row={r} />)}
    </ul>
  );
}
