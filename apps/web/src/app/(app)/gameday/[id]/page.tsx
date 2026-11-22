import type { Metadata } from "next";

import { ArrowUpRight } from "@phosphor-icons/react/ssr";
import Link from "next/link";
import { notFound } from "next/navigation";

import { EventScore } from "@/components/gameday/event-score";
import { Networks } from "@/components/gameday/game-row";
import { GameView, type StripGame } from "@/components/gameday/game-view";
import { requireOwner } from "@/lib/auth";
import { loadEvent, loadSlate, type GameEvent } from "@/lib/gameday/data";
import { loadSummary } from "@/lib/gameday/espn";
import {
  dateLabel, gameDay, kalshiUrl, minusSign, startLabel, timeSlot, type Golfer, type Score, type SlateRow,
} from "@/lib/gameday/slate";
import { slateSides, slateTeam, type TeamRow } from "@/lib/gameday/teams";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Game" };

// One event (Chat 7 Phase 3; docs/gameday.md §5): score and clock (live,
// polled), where to watch, the Kalshi event (a link, or why not), venue and
// neutral site, golf's leaderboard with the followed players, an F1
// weekend's sessions, and the empty "Your bets" slot (Chat 9).

type Face = { rank?: number; loc?: string; nick?: string; logo?: string; color?: string };
type Participants = { home?: Face; away?: Face };

/** College teams go by the school (Google: "Oregon", "MI State"); the pros by the nickname ("Knicks"). */
const BY_SCHOOL = new Set(["cfb", "mcbb"]);

/** The team-game page (owner, Nov 21): Google's layout, ESPN's summary read on open. */
async function TeamGame({ ev, row, supabase }: { ev: GameEvent; row: SlateRow; supabase: Awaited<ReturnType<typeof createClient>> }) {
  const p = (ev.participants && !Array.isArray(ev.participants) ? ev.participants : {}) as Participants;
  const faces = slateSides({
    id: ev.id,
    aloc: p.away?.loc ?? null, an: p.away?.nick ?? null, al: p.away?.logo ?? null, ac: p.away?.color ?? null,
    hloc: p.home?.loc ?? null, hn: p.home?.nick ?? null, hl: p.home?.logo ?? null, hc: p.home?.color ?? null,
  } satisfies TeamRow);
  const away = slateTeam(faces?.[0], ev.away!, false);
  const home = slateTeam(faces?.[1], ev.home!, true);
  const name = (t: typeof away) => (BY_SCHOOL.has(ev.league) ? t.city : (t.nick ?? t.city));

  // The summary and the day's other games in the league, together.
  const [sum, slate] = await Promise.all([
    loadSummary(supabase, ev.id),
    loadSlate(supabase, { day: ev.day, all: true, withTests: ev.is_test }).catch((e: Error) => {
      console.error(`gameday: strip unread: ${e.message}`);
      return [] as SlateRow[];
    }),
  ]);
  const strip: StripGame[] = slate
    .filter((r) => r.league === ev.league && r.home && r.away)
    .slice(0, 30)
    .map((r) => ({ id: r.id, row: r, away: slateTeam(r.tm?.[0], r.away!, false), home: slateTeam(r.tm?.[1], r.home!, true) }));

  return (
    <GameView
      initial={row}
      initialSummary={sum.summary}
      teams={{ away, home }}
      names={{ away: name(away), home: name(home) }}
      ranks={{ away: p.away?.rank, home: p.home?.rank }}
      leagueLabel={ev.league_label}
      today={gameDay(new Date().toISOString())}
      kalshi={ev.kalshi}
      venue={ev.venue}
      neutral={!!ev.neutral_site}
      tv={ev.broadcast}
      strip={strip}
    >
      {!sum.summary && !ev.is_test && (
        <p data-testid="summary-unavailable" className="mt-4 text-sm text-text-muted">{sum.reason} Stats and plays will show when it does.</p>
      )}
      <section aria-label="Your bets" data-testid="detail-bets" className="mt-8 border-t border-border pt-4">
        <h2 className="text-xl text-text">Your bets</h2>
        <p className="mt-2 text-base text-text-muted">No bets on this game. Your bets attach here once bet tracking ships (Chat 9).</p>
      </section>
    </GameView>
  );
}

/** The detail's event in the slate's row shape, the score trimmed as db/068 gameday_score does. */
function asRow(ev: GameEvent): SlateRow {
  const p = (ev.participants && !Array.isArray(ev.participants) ? ev.participants : {}) as Participants;
  const raw = (ev.score ?? undefined) as Record<string, unknown> | undefined;
  let score: Score | undefined = raw as Score | undefined;
  if (raw && ev.kind === "golf_round") {
    score = { round: raw.round as number, leader: (raw.leaders as Golfer[] | undefined)?.[0], followed: raw.followed as Golfer[] };
  } else if (raw && ev.kind === "race_session") {
    score = { leader: (raw.results as Golfer[] | undefined)?.[0] };
  }
  return {
    id: ev.id, league: ev.league, day: ev.day, at: ev.starts_at, tbd: ev.time_tbd || undefined,
    title: ev.home && ev.away ? undefined : ev.title, session: ev.session ?? undefined,
    home: ev.home ?? undefined, away: ev.away ?? undefined, neutral: ev.neutral_site || undefined,
    hr: p.home?.rank, ar: p.away?.rank, tv: ev.broadcast, tier: ev.tv_tier, status: ev.status,
    detail: ev.status_detail ?? undefined, clock: ev.clock ?? undefined, score, test: ev.is_test || undefined,
  };
}

function Section({ title, testId, children }: { title: string; testId: string; children: React.ReactNode }) {
  return (
    <section aria-label={title} data-testid={testId} className="mt-10 border-t border-border pt-4">
      <h2 className="label-mono text-text-muted">{title}</h2>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function KalshiLine({ ev }: { ev: GameEvent }) {
  const k = ev.kalshi;
  if (k.state === "linked" && k.ticker && k.series) {
    return (
      <a data-testid="kalshi-link" href={kalshiUrl(k.series, k.ticker)} target="_blank" rel="noopener noreferrer"
         className="tap inline-flex items-center gap-1.5 text-base text-accent underline-offset-4 hover:underline">
        Open on Kalshi <ArrowUpRight aria-hidden size={16} />
        <span className="ml-1 font-mono text-xs text-text-muted">{k.ticker}</span>
      </a>
    );
  }
  if (k.state === "not_listed") {
    return <p data-testid="kalshi-state" data-state="not_listed" className="text-base text-text">Not listed yet</p>;
  }
  if (k.state === "not_linked" || k.state === "out_of_scope") {
    return (
      <div data-testid="kalshi-state" data-state={k.state}>
        <p className="text-base text-text">Not linked</p>
        {k.reason && <p data-testid="kalshi-reason" className="mt-1 text-sm text-text-muted">{k.reason}</p>}
      </div>
    );
  }
  return <p data-testid="kalshi-state" data-state="unchecked" className="text-base text-text-muted">Not checked yet</p>;
}

function GolfBoard({ ev }: { ev: GameEvent }) {
  const s = ev.standings;
  if (!s) return <p className="text-base text-text-muted">No scores yet.</p>;
  const leaders = (s.score.leaders as Golfer[] | undefined) ?? [];
  const followed = (s.score.followed as Golfer[] | undefined) ?? [];
  const row = (g: Golfer) => (
    <li key={g.name} className="grid grid-board gap-3 border-b border-border py-2 text-base">
      <span className="font-mono tabular-nums text-text-muted">{g.pos ?? ""}</span>
      <span className="text-text">{g.name}</span>
      <span className="font-mono tabular-nums text-text">
        {minusSign(g.to_par)}{g.thru != null && g.thru !== "" ? <span className="text-text-muted"> · thru {g.thru}</span> : null}
      </span>
    </li>
  );
  return (
    <>
      <p className="label-mono text-text-muted">{s.session} · {s.status === "final" ? "final" : "in play"}</p>
      <ul className="mt-2 border-t border-border">{leaders.map(row)}</ul>
      <p className="mt-4 label-mono text-text-muted">Scheffler and Koepka</p>
      {followed.length > 0 ? (
        <ul data-testid="golf-followed" className="mt-2 border-t border-border">{followed.map(row)}</ul>
      ) : (
        <p data-testid="golf-followed-none" className="mt-2 text-base text-text-muted">Neither is in the field.</p>
      )}
    </>
  );
}

export default async function GameDetailPage({ params }: { params: Promise<{ id: string }> }) {
  await requireOwner();
  const { id: raw } = await params;
  const id = Number(raw);
  if (!Number.isSafeInteger(id) || id <= 0) notFound();
  const supabase = await createClient();
  const ev = await loadEvent(supabase, id);
  if (!ev) notFound();
  const row = asRow(ev);

  if (ev.kind === "game" && ev.home && ev.away) {
    return (
      <article data-testid="game-detail" data-id={ev.id} className="lg:max-w-3xl">
        <h1 className="sr-only">{ev.title}</h1>
        <TeamGame ev={ev} row={row} supabase={supabase} />
      </article>
    );
  }

  return (
    <article data-testid="game-detail" data-id={ev.id} className="lg:max-w-3xl">
      <Link href="/gameday" className="tap label-mono inline-flex items-center text-text-muted hover:text-text">
        ← Game Day
      </Link>
      <p className="mt-6 label-mono text-text-muted">
        {ev.league_label}{ev.home && ev.away ? "" : ` · ${ev.event_name}`}
        {ev.is_test && <span className="ml-2 rounded-pill border border-border px-1.5">test</span>}
      </p>
      <h1 className="sr-only">{ev.title}</h1>
      <div className="mt-3">
        <EventScore initial={row} when={dateLabel(ev.day)} />
      </div>

      <Section title="Where to watch" testId="detail-watch">
        <p className="text-base text-text">
          {dateLabel(ev.day)} · {timeSlot(row)}
        </p>
        <p className="mt-1 text-base text-text">
          <Networks tv={ev.broadcast} testId="detail-tv" />
        </p>
      </Section>

      {ev.kind === "golf_round" && (
        <Section title="Leaderboard" testId="detail-golf"><GolfBoard ev={ev} /></Section>
      )}

      {ev.kind === "race_session" && ev.sessions && (
        <Section title="This weekend" testId="detail-sessions">
          <ul className="border-t border-border">
            {ev.sessions.map((s) => (
              <li key={s.id} className="grid grid-score gap-3 border-b border-border py-2 text-base">
                <span className={s.id === ev.id ? "text-text" : "text-text-muted"}>
                  {s.session === "Qual" ? "Qualifying" : s.session}
                </span>
                <span className="font-mono tabular-nums text-text">
                  {s.status === "final" ? "Final" : `${dateLabel(gameDay(s.starts_at, s.time_tbd))} · ${startLabel({ at: s.starts_at, tbd: s.time_tbd })}`}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-sm text-text-muted">Practice sessions aren&apos;t stored.</p>
        </Section>
      )}

      <Section title="Kalshi" testId="detail-kalshi"><KalshiLine ev={ev} /></Section>

      <Section title="Venue" testId="detail-venue">
        <p className="text-base text-text">{ev.venue ?? "Not listed"}</p>
        {ev.neutral_site && <p data-testid="detail-neutral" className="mt-1 text-sm text-text-muted">Neutral site</p>}
      </Section>

      <Section title="Your bets" testId="detail-bets">
        <p className="text-base text-text-muted">No bets on this game. Your bets attach here once bet tracking ships (Chat 9).</p>
      </Section>
    </article>
  );
}
