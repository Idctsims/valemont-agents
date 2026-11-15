"use client";

import { useBetting } from "@/components/betting/betting-store";
import { cardPrices, type BoardCard } from "@/lib/betting/board";
import { legFrom, sideOf } from "@/lib/betting/combo";
import { priceText } from "@/lib/betting/price";

// A board card's moneyline prices (owner, Nov 15; db/082): Kalshi's own ask
// for each side, in the chosen unit, labelled with Kalshi's code for it so a
// price can never sit under the wrong team. A tap adds YES to the slip, as on
// the game page; tapping one already in the slip takes it out. No consensus
// here: the game page's header carries it.

export function CardPrices({ card, sport }: { card: Pick<BoardCard, "g" | "t" | "w" | "wy">; sport: string }) {
  const { unit, legs, tick } = useBetting();
  const prices = cardPrices(card);
  if (prices.length === 0) {
    return <p data-testid="card-no-ml" className="px-4 pb-3 text-xs text-text-muted">No moneyline open.</p>;
  }
  return (
    <div data-testid="card-ml" className={`grid gap-1.5 px-4 pb-3 ${prices.length === 2 ? "grid-cols-2" : "grid-tiles"}`}>
      {prices.map((p) => {
        const side = sideOf(legs, p.t);
        const on = side !== null;
        return (
          <button key={p.t} type="button" data-testid="card-ml-pick" data-ticker={p.t} data-side={side ?? undefined}
                  aria-pressed={on}
                  aria-label={`${p.code} to win, ${card.t}: ${priceText(p.a, unit)}${on ? ", in the slip" : ""}`}
                  onClick={() => tick(legFrom({ t: p.t, label: `${p.code} to win`, game: card.t, sport }, side ?? "yes",
                                              p, card.wy === true))}
                  className={`flex min-h-12 items-center justify-between gap-2 rounded-tile border px-3 py-1.5 transition-colors ${
                    on ? "border-accent bg-accent text-on-accent" : "border-border bg-bg text-text hover:border-accent"}`}>
            <span className={`label-mono ${on ? "" : "text-text-muted"}`}>{side === "no" ? `NO ${p.code}` : p.code}</span>
            <span data-testid="card-ml-price" className="font-mono text-lg tabular-nums">{priceText(p.a, unit)}</span>
          </button>
        );
      })}
    </div>
  );
}
