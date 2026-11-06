"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { pollLive } from "@/app/(app)/gameday/actions";
import { StatusChip } from "@/components/ui/badges";
import {
  POLL_SECONDS, applyPatches, followedLine, isLive, kindOf, leaderLine, pollIds, rankPrefix, startLabel, statusLabel,
  type SlateRow,
} from "@/lib/gameday/slate";

// The detail page's score and clock header, polled like the slate: every
// POLL_SECONDS while the tab is visible and this game is in play or due.

export function EventScore({ initial, when }: { initial: SlateRow; when: string }) {
  const [row, setRow] = useState(initial);
  const [polls, setPolls] = useState(0);
  const rowRef = useRef(row);
  useEffect(() => {
    rowRef.current = row;
  }, [row]);
  // eslint-disable-next-line react-hooks/purity
  const hot = useMemo(() => pollIds([row], Date.now()).length > 0, [row]);

  const poll = useCallback(async () => {
    if (document.visibilityState !== "visible") return;
    const ids = pollIds([rowRef.current], Date.now());
    if (ids.length === 0) return;
    const res = await pollLive(ids);
    if (res.ok) setRow((cur) => applyPatches([cur], res.rows)[0]);
    setPolls((n) => n + 1);
  }, []);

  useEffect(() => {
    if (!hot) return;
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

  const live = isLive(row);
  const status = statusLabel(row);
  const kind = kindOf(row.league);
  const scored = !!row.score && (live || row.status === "final" || row.status === "suspended");

  return (
    <div data-testid="event-score" data-status={row.status} data-polling={hot ? "true" : "false"} data-polls={polls}>
      <div className="flex min-h-7 flex-wrap items-center gap-3">
        {live ? (
          <StatusChip status="live" label={status ?? "Live"} />
        ) : (
          <p data-testid="event-when" className="label-mono text-text-muted">
            {status ?? `${when} · ${startLabel(row)}`}
          </p>
        )}
        {live && row.clock && <span className="label-mono text-text-muted">{row.clock}</span>}
      </div>

      {kind === "game" && row.home && row.away ? (
        <div className="mt-4 grid grid-score items-baseline gap-x-6 gap-y-2">
          <span className="font-display text-2xl text-text">
            {rankPrefix(row.ar) && <span className="font-mono text-base text-text-muted">{rankPrefix(row.ar)}</span>}
            {row.away}
          </span>
          <span data-testid="event-score-away" className="font-mono text-2xl tabular-nums text-text">
            {scored ? row.score?.away : ""}
          </span>
          <span className="font-display text-2xl text-text">
            <span className="font-mono text-base text-text-muted">{row.neutral ? "vs " : "at "}</span>
            {rankPrefix(row.hr) && <span className="font-mono text-base text-text-muted">{rankPrefix(row.hr)}</span>}
            {row.home}
          </span>
          <span data-testid="event-score-home" className="font-mono text-2xl tabular-nums text-text">
            {scored ? row.score?.home : ""}
          </span>
        </div>
      ) : (
        <div className="mt-4">
          <p className="font-display text-2xl text-text">{row.title}</p>
          {row.session && <p className="mt-1 text-base text-text-muted">{row.session}</p>}
          {scored && row.score?.leader?.name && (
            <p data-testid="event-leader" className="mt-3 text-base text-text">
              Leader: {kind === "golf_round" ? leaderLine(row.score.leader) : row.score.leader.name}
            </p>
          )}
          {scored && kind === "golf_round" && (row.score?.followed ?? []).map((g) => (
            <p key={g.name} data-testid="event-followed" className="text-base text-text-muted">{followedLine(g)}</p>
          ))}
          {scored && row.score?.sets && (
            <p className="mt-3 font-mono text-xl tabular-nums text-text">
              {row.score.sets.map((s) => s.join("–")).join("  ")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
