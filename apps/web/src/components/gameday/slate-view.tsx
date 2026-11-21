"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { pollLive } from "@/app/(app)/gameday/actions";
import { GameRow } from "@/components/gameday/game-row";
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

  return (
    <div data-testid="gameday-slate" data-polling={hot > 0 ? "true" : "false"} data-polls={polls}
         className="flex flex-col gap-8">
      {error && (
        <p role="status" className="text-sm text-danger">{error} It will try again in a minute.</p>
      )}

      {sections.live.length > 0 && (
        <section aria-label="Live now" data-testid="section-live">
          <Band title="Live now" n={sections.live.length} />
          <Cards rows={sections.live} />
        </section>
      )}

      <section aria-label="Tonight" data-testid="section-tonight">
        <Band title="Tonight" note={dateLabel(today)} n={sections.tonight.length} />
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

      <section aria-label="This week" data-testid="section-week" className="flex flex-col gap-6">
        <div>
          <h2 className="font-display text-3xl text-text">This week</h2>
          {all && <p className="mt-1 label-mono text-text-muted">The five only</p>}
        </div>
        {sections.week.length > 0 ? (
          sections.week.map(({ day, rows: dayRows }) => (
            <div key={day} data-testid="week-day" data-day={day}>
              <Band title={dayLabel(day, today)} n={dayRows.length} level={3} />
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
  );
}

/** The betting board's green day band: the name, and how many. */
function Band({ title, note, n, level = 2 }: { title: string; note?: string; n: number; level?: 2 | 3 }) {
  const H = level === 2 ? "h2" : "h3";
  return (
    <div className="flex items-center justify-between rounded-t-tile bg-accent px-4 py-2.75 text-on-accent">
      <H className="text-sm font-semibold">
        {title}{note && <span className="label-mono ml-2 opacity-85">{note}</span>}
      </H>
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
