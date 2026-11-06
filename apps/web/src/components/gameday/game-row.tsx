import Link from "next/link";

import { StatusChip } from "@/components/ui/badges";
import { serviceFor } from "@/lib/gameday/services";
import {
  LEAGUES, followedLine, isLive, kindOf, leaderLine, rankPrefix, statusLabel, timeSlot, tvItems, type SlateRow,
} from "@/lib/gameday/slate";

// One event on Game Day: a hairline row (the feed's ledger language), the
// whole row a link to /gameday/[id]. Mono "NFL · 7:15 PM" (a live golf round:
// its round; once over: its status), the matchup with ranks, the score once
// live, and each network once: a gold chip when it is on the owner's
// services, plain text otherwise ("Not listed" when ESPN gives none). No
// Kalshi price here (Chat 8).

/** One network: the gold chip when it streams on the owner's service (named in its title), else plain text. */
function Network({ name, service }: { name: string; service: string | null }) {
  if (!service) return <span data-testid="network">{name}</span>;
  return (
    <span
      data-testid="service-mark"
      data-service={service}
      title={`On your ${service}`}
      aria-label={`${name}, on your ${service}`}
      className="label-mono inline-flex h-6 shrink-0 items-center whitespace-nowrap rounded-pill bg-accent-strong px-2.5 text-on-accent-strong"
    >
      {name}
    </span>
  );
}

/** The networks line, shared by the card and the detail page. */
export function Networks({ tv, testId = "game-tv" }: { tv: readonly string[] | null | undefined; testId?: string }) {
  const items = tvItems(tv, serviceFor);
  return (
    <span data-testid={testId} className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
      {items.length === 0
        ? "Not listed"
        : items.map((it, i) => (
            <span key={it.name} className="inline-flex items-center gap-x-2">
              {i > 0 && <span aria-hidden className="text-text-muted">·</span>}
              <Network name={it.name} service={it.service} />
            </span>
          ))}
    </span>
  );
}

function Score({ value }: { value: number | string | undefined }) {
  return <span className="font-mono text-base tabular-nums text-text">{value ?? ""}</span>;
}

export function GameRow({ row }: { row: SlateRow }) {
  const live = isLive(row);
  const status = statusLabel(row);
  const kind = kindOf(row.league);
  const showScore = !!row.score && (live || row.status === "final" || row.status === "suspended");
  const tag = LEAGUES[row.league]?.short ?? row.league.toUpperCase();

  return (
    <li data-testid="game-row" data-id={row.id} data-league={row.league} data-status={row.status}
        data-bama={row.bama ? "true" : undefined} className="border-b border-border">
      <Link href={`/gameday/${row.id}`} className="block py-4 transition-colors hover:bg-surface-2">
        <div className="flex min-h-7 items-center justify-between gap-3">
          <p className="label-mono text-text-muted">
            {tag} · <span data-testid="game-start">{timeSlot(row)}</span>
            {row.test && <span className="ml-2 rounded-pill border border-border px-1.5">test</span>}
          </p>
          {live && <StatusChip status="live" label={status ?? "Live"} />}
        </div>

        {kind === "game" && row.home && row.away ? (
          <div className="mt-2 grid grid-score items-baseline gap-x-4 gap-y-1">
            <span className="text-base text-text">
              {rankPrefix(row.ar) && <span className="font-mono text-sm text-text-muted">{rankPrefix(row.ar)}</span>}
              {row.away}
            </span>
            {showScore ? <span data-testid="score-away"><Score value={row.score?.away} /></span> : <span />}
            <span className="text-base text-text">
              <span className="font-mono text-sm text-text-muted">{row.neutral ? "vs " : "at "}</span>
              {rankPrefix(row.hr) && <span className="font-mono text-sm text-text-muted">{rankPrefix(row.hr)}</span>}
              {row.home}
            </span>
            {showScore ? <span data-testid="score-home"><Score value={row.score?.home} /></span> : <span />}
          </div>
        ) : (
          <div className="mt-2">
            <p className="text-base text-text">{row.title}</p>
            {row.session && <p className="text-sm text-text-muted">{row.session}</p>}
            {showScore && row.score?.leader?.name && (
              <p data-testid="score-leader" className="mt-1 text-sm text-text">
                Leader: {kind === "golf_round" ? leaderLine(row.score.leader) : row.score.leader.name}
              </p>
            )}
            {showScore && kind === "golf_round" && (row.score?.followed ?? []).map((g) => (
              <p key={g.name} className="text-sm text-text-muted">{followedLine(g)}</p>
            ))}
            {showScore && row.score?.sets && (
              <p className="font-mono text-sm tabular-nums text-text">
                {row.score.sets.map((s) => s.join("–")).join("  ")}
              </p>
            )}
          </div>
        )}

        <p className="mt-2 text-sm text-text-muted">
          <Networks tv={row.tv} />
        </p>
      </Link>
    </li>
  );
}
