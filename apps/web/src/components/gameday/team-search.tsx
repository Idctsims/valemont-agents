"use client";

import { MagnifyingGlass, X } from "@phosphor-icons/react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { findTeams } from "@/app/(app)/gameday/actions";
import { TeamLogo } from "@/components/betting/matchup";
import { LiveMark } from "@/components/gameday/game-row";
import type { TeamHit } from "@/lib/gameday/search";
import { SEARCH_MIN } from "@/lib/gameday/search";
import { LEAGUES, dateLabel, gameDay, isLive, startLabel } from "@/lib/gameday/slate";
import { slateTeam } from "@/lib/gameday/teams";

// The magnifying glass at Game Day's top right (owner, Nov 21): it opens a
// search box; as the owner types, teams appear with their logos (from the
// games in sports_events, search.ts), each opening its game: live, else the
// next, else the last. Asked 200 ms after the last keystroke; a slower,
// older answer never replaces a newer one.

const DEBOUNCE_MS = 200;

function when(h: TeamHit): string {
  if (isLive(h)) return "";
  if (h.status === "final") return `Final · ${dateLabel(gameDay(h.at))}`;
  if (h.status === "postponed" || h.status === "canceled") return h.status === "postponed" ? "Postponed" : "Canceled";
  return `${dateLabel(gameDay(h.at))} · ${startLabel({ at: h.at })}`;
}

export function TeamSearch({ search = findTeams }: { search?: typeof findTeams } = {}) {
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const latest = useRef("");
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<TeamHit[]>([]);
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");

  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const onType = (value: string) => {
    setQ(value);
    const text = value.trim();
    latest.current = text;
    window.clearTimeout(timer.current);
    if (text.replace(/[^\p{L}\p{N}]/gu, "").length < SEARCH_MIN) {
      setHits([]);
      setState("idle");
      return;
    }
    setState("busy");
    timer.current = window.setTimeout(async () => {
      const res = await search(text);
      if (latest.current !== text) return; // a newer search is on its way
      if (res.ok) {
        setHits(res.teams);
        setState("done");
      } else {
        setState("error");
      }
    }, DEBOUNCE_MS);
  };

  const open = () => {
    dialog.current?.showModal();
    input.current?.focus();
    input.current?.select();
  };
  const close = () => dialog.current?.close();

  return (
    <>
      <button type="button" onClick={open} data-testid="team-search-open" aria-label="Search teams"
              className="tap inline-flex size-11 items-center justify-center rounded-pill border border-border bg-surface text-text hover:border-accent-ink">
        <MagnifyingGlass size={20} aria-hidden />
      </button>

      <dialog ref={dialog} data-testid="team-search" aria-label="Search teams"
              onClick={(e) => { if (e.target === dialog.current) close(); }}
              className="m-0 mx-auto mt-4 w-full max-w-xl bg-transparent p-4 text-text backdrop:bg-bg/80 backdrop:backdrop-blur-sm sm:mt-16">
        <div className="overflow-hidden rounded-inner border border-border bg-surface">
          <form role="search" onSubmit={(e) => {
            e.preventDefault();
            if (hits[0]) {
              close();
              router.push(`/gameday/${hits[0].gameId}`);
            }
          }} className="flex items-center gap-2 border-b border-border px-4">
            <MagnifyingGlass size={20} aria-hidden className="shrink-0 text-text-muted" />
            <input ref={input} type="text" inputMode="search" value={q} onChange={(e) => onType(e.target.value)}
                   data-testid="team-search-input" placeholder="Search teams" aria-label="Team name"
                   autoComplete="off" autoCorrect="off" spellCheck={false} enterKeyHint="go"
                   className="min-h-14 min-w-0 flex-1 bg-transparent text-lg text-text outline-none placeholder:text-text-muted" />
            <button type="button" onClick={close} aria-label="Close search"
                    className="tap inline-flex size-10 shrink-0 items-center justify-center rounded-pill text-text-muted hover:text-text">
              <X size={20} aria-hidden />
            </button>
          </form>

          <div aria-live="polite" className="max-h-search overflow-y-auto">
            {state === "idle" && (
              <p className="px-4 py-5 text-sm text-text-muted">Type a team: a school, a city or a nickname.</p>
            )}
            {state === "busy" && hits.length === 0 && <p className="px-4 py-5 text-sm text-text-muted">Searching…</p>}
            {state === "error" && <p className="px-4 py-5 text-sm text-danger">Couldn&apos;t search right now. Try again.</p>}
            {state === "done" && hits.length === 0 && (
              <p className="px-4 py-5 text-sm text-text-muted">No team with a game from the last two weeks on.</p>
            )}
            {hits.length > 0 && (
              <ul data-testid="team-search-results" className={state === "busy" ? "opacity-60" : ""}>
                {hits.map((h) => {
                  const team = slateTeam(h.face, h.name, h.home);
                  const live = isLive(h);
                  return (
                    <li key={`${h.league}|${h.name}`}>
                      <Link href={`/gameday/${h.gameId}`} onClick={close} data-testid="team-hit" data-game={h.gameId}
                            className="flex min-h-16 items-center gap-3 border-b border-border px-4 py-2.5 hover:bg-band focus-visible:bg-band">
                        <span className="flex size-9 shrink-0 items-center justify-center">
                          {team.logo ? <TeamLogo team={team} size="row" /> : (
                            <span aria-hidden className="text-sm font-semibold text-text-muted">{h.name.slice(0, 1)}</span>
                          )}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span data-testid="team-hit-name" className="block truncate text-base text-text">{h.name}</span>
                          <span className="flex items-center gap-1.5 truncate text-sm text-text-muted">
                            <span className="label-mono">{LEAGUES[h.league]?.short ?? h.league.toUpperCase()}</span>
                            <span aria-hidden>·</span>
                            {live && <LiveMark />}
                            <span className="truncate">{when(h)}{when(h) ? " · " : " "}{h.home ? "vs" : "at"} {h.opponent}</span>
                          </span>
                        </span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      </dialog>
    </>
  );
}
