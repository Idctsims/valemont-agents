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
         className="lg:max-w-3xl">
      {error && (
        <p role="status" className="mb-4 text-sm text-danger">{error} It will try again in a minute.</p>
      )}

      {sections.live.length > 0 && (
        <section aria-label="Live now" data-testid="section-live" className="mb-10">
          <h2 className="font-display text-2xl text-text">Live now</h2>
          <ul className="mt-3 border-t border-border">
            {sections.live.map((r) => <GameRow key={r.id} row={r} />)}
          </ul>
        </section>
      )}

      <section aria-label="Tonight" data-testid="section-tonight" className="mb-10">
        <h2 className="font-display text-2xl text-text">
          Tonight <span className="label-mono align-middle text-text-muted">{dateLabel(today)}</span>
        </h2>
        {sections.tonight.length > 0 ? (
          <ul className="mt-3 border-t border-border">
            {sections.tonight.map((r) => <GameRow key={r.id} row={r} />)}
          </ul>
        ) : (
          <p data-testid="tonight-empty" className="mt-3 border-t border-border pt-4 text-base text-text-muted">
            {sections.live.length > 0
              ? "Nothing else on tonight."
              : all ? "Nothing on tonight." : "Nothing on tonight across the five. All sports shows the rest."}
          </p>
        )}
      </section>

      <section aria-label="This week" data-testid="section-week" className="mb-10">
        <h2 className="font-display text-2xl text-text">This week</h2>
        {all && <p className="mt-1 label-mono text-text-muted">The five only</p>}
        {sections.week.length > 0 ? (
          sections.week.map(({ day, rows: dayRows }) => (
            <div key={day} data-testid="week-day" data-day={day} className="mt-6">
              <h3 className="label-mono text-text-muted">{dayLabel(day, today)}</h3>
              <ul className="mt-2 border-t border-border">
                {dayRows.map((r) => <GameRow key={r.id} row={r} />)}
              </ul>
            </div>
          ))
        ) : (
          <p data-testid="week-empty" className="mt-3 border-t border-border pt-4 text-base text-text-muted">
            Nothing scheduled across the five in the next 7 days.
          </p>
        )}
      </section>
    </div>
  );
}
