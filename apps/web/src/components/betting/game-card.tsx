"use client";

import Link from "next/link";

import { useBetting } from "@/components/betting/betting-store";
import { ConsensusBar, Matchup, type PricePick } from "@/components/betting/matchup";
import { cardPrices, type BoardCard } from "@/lib/betting/board";
import { legFrom, sideOf } from "@/lib/betting/combo";
import { priceText } from "@/lib/betting/price";
import { cardBar, cardLines, cardTeams, cardTitle, type MainLine } from "@/lib/betting/teams";

// A game on the board, as the approved mock draws it (owner, Nov 15; db/083):
// the start and league; each team's logo, name and Kalshi's price to win; the
// books' consensus in the teams' colours; the main spread and total; and the
// way into the game's own page. Every price taps into the slip (YES).

const CT = "America/Chicago";

function timeLabel(at: number): string {
  return `${new Date(at * 1000).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: CT })} CT`;
}

function Cell({ kind, line, game, sport }: { kind: string; line: MainLine | undefined; game: string; sport: string }) {
  const { unit, legs, tick } = useBetting();
  if (!line) {
    return (
      <span className="flex min-h-13.5 min-w-0 flex-col justify-center px-4 py-2.5">
        <span className="text-xs text-text-muted">{kind}</span>
      </span>
    );
  }
  const side = sideOf(legs, line.t);
  const on = side !== null;
  const label = `${kind}: ${line.label}`;
  return (
    <button type="button" data-testid="card-line" data-kind={kind.toLowerCase()} data-ticker={line.t} aria-pressed={on}
            aria-label={`${label}: ${priceText(line.a, unit)}${on ? ", in the slip" : ""}`}
            onClick={() => tick(legFrom({ t: line.t, label, game, sport }, side ?? "yes", line, line.yesOnly))}
            className={`flex min-h-13.5 min-w-0 items-center justify-between gap-2 px-4 py-2.5 text-left transition-colors ${
              on ? "bg-pick shadow-pick" : "hover:bg-surface-2"}`}>
      <span className="min-w-0">
        <span className="block text-xs text-text-muted">{kind}</span>
        <span className="block truncate text-xs font-semibold text-text">{side === "no" ? `NO ${line.label}` : line.label}</span>
      </span>
      <span data-testid="card-line-price" className="font-mono text-base tabular-nums tracking-tight text-text">
        {priceText(line.a, unit)}
      </span>
    </button>
  );
}

export function GameCard({ card, sport }: { card: BoardCard; sport: string }) {
  const title = cardTitle(card);
  const teams = cardTeams(card);
  const bar = cardBar(card);
  const lines = cardLines(card);
  const href = `/betting/game/${encodeURIComponent(card.g)}`;
  const picks: PricePick[] = cardPrices(card).map((p, i) => ({
    t: p.t, b: p.b, a: p.a, yesOnly: card.wy === true, game: title, sport, tm: p.code,
    label: `${teams?.[i]?.city ?? p.code} to win`,
  }));
  return (
    <article data-testid="board-game" data-game={card.g} data-league={card.lg} aria-label={title}
             className="flex min-w-0 flex-col overflow-hidden rounded-inner border border-border bg-surface">
      <p className="flex justify-between gap-3 border-b border-border px-4 py-2 text-xs text-text-muted">
        <span data-testid="game-start" className="font-semibold text-text">{timeLabel(card.at)}</span>
        <span className="flex items-center gap-2">
          {card.hc && <span data-testid="card-claimed" className="label-mono text-hit">claimed edge</span>}
          {card.test && <span className="label-mono rounded-pill border border-border px-1.5">test</span>}
          <span>{(card.lg ?? sport).toUpperCase()}</span>
        </span>
      </p>
      {teams ? (
        <div data-testid="card-ml">
          <Matchup teams={teams} picks={picks} />
        </div>
      ) : (
        <div data-testid="card-ml" className="flex flex-col gap-2 px-4 pb-3.5 pt-4">
          <p className="font-display text-xl text-text">{title}</p>
          <Matchup teams={picks.slice(0, 2).map((p, i) => ({ city: p.tm ?? "", home: i === 1 }))} picks={picks} />
        </div>
      )}
      {bar && teams && <ConsensusBar teams={teams} p={bar.p} source={bar.source} />}
      {(lines.spread || lines.total) && (
        <div className="grid grid-cols-2 divide-x divide-border border-t border-border">
          <Cell kind="Spread" line={lines.spread} game={title} sport={sport} />
          <Cell kind="Total" line={lines.total} game={title} sport={sport} />
        </div>
      )}
      <Link href={href} data-testid="board-game-open"
            className="mt-auto flex min-h-13 items-center justify-between gap-3 border-t border-border bg-surface-2 px-4 py-2.5 hover:bg-pick">
        <span>
          <span className="block text-xs text-text-muted"><b className="font-semibold text-text">{card.n ?? 0}</b> markets</span>
          <span className="block text-sm font-semibold text-accent-ink">Players, other lines, quarters</span>
        </span>
        <span aria-hidden className="text-accent-ink">→</span>
      </Link>
    </article>
  );
}
