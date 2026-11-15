"use client";

import { useState } from "react";

import { useBetting } from "@/components/betting/betting-store";
import { QuoteView, usePricing } from "@/components/betting/combo-quote";
import { MIN_LEGS, noRefusal, pricingLabel, type Side } from "@/lib/betting/combo";
import { priceText, ticketTotal, totalText } from "@/lib/betting/price";

// The slip (the approved mock): a persistent panel beside the board on
// desktop; on the phone a bar docked above the tab bar that opens to a
// full-height sheet. One element for both, so its state (and a price being
// fetched) survives a resize. Each leg flips YES / NO in place (owner, Nov 15;
// NO refused, with the reason, where the event takes YES only in a combo).
// Legs show their price BEFORE fee, labelled; the
// ticket's total is AFTER fee, Kalshi's one taker fee on the combo's price.
// Combo pricing underneath is Phase 3's (db/076, db/077). Nothing here bets.

function feeCents(v: number): string {
  return `${(Math.round(v * 10000) / 100).toFixed(2)}¢`;
}

export function Slip() {
  const { legs, unit, remove, clear, flip } = useBetting();
  const { phase, now, ask, busy } = usePricing(legs);
  const [open, setOpen] = useState(false);
  const n = legs.length;
  const total = ticketTotal(legs.map((l) => l.a));
  const games = new Set(legs.map((l) => l.game)).size;
  const head = n === 0 ? "Empty" : `${n} ${n === 1 ? "leg" : "legs"}${total ? ` · ${totalText(total, unit)}` : ""}`;

  return (
    <aside data-testid="combo-sheet" aria-label="Slip" aria-busy={busy} data-open={open ? "1" : "0"}
           className={`${n === 0 ? "hidden lg:flex" : "flex"} fixed inset-x-0 z-40 flex-col border-t border-border bg-surface ${
             open ? "top-0 bottom-dock pt-safe" : "bottom-dock"}
             lg:sticky lg:top-8 lg:bottom-auto lg:z-auto lg:max-h-screen lg:overflow-hidden lg:rounded-inner lg:border lg:pt-0`}>
      <button type="button" data-testid="combo-sheet-toggle" aria-expanded={open} onClick={() => setOpen(!open)}
              className="tap flex w-full shrink-0 items-center justify-between gap-3 bg-accent px-safe text-on-accent lg:pointer-events-none lg:px-4">
        <span className="text-base">Slip</span>
        <span data-testid="slip-head" className="label-mono">{head}</span>
        <span aria-hidden className="font-mono lg:hidden">{open ? "↓" : "↑"}</span>
      </button>

      <div className={`${open ? "flex" : "hidden"} min-h-0 flex-1 flex-col overflow-y-auto lg:flex`}>
        {n === 0 ? (
          <p data-testid="slip-empty" className="px-4 py-6 text-sm text-text-muted">Tap any price to start a slip.</p>
        ) : (
          <>
            <p className="label-mono border-b border-border px-4 py-2 text-text-muted">Leg prices before fee</p>
            <ul data-testid="combo-legs" className="divide-y divide-border border-b border-border">
              {legs.map((l) => {
                const why = noRefusal(l);
                const sides: { s: Side; face: string; p: number | undefined; off: string | null }[] = [
                  { s: "yes", face: "YES", p: l.py ?? (l.side === "yes" ? l.a : undefined), off: null },
                  { s: "no", face: "NO", p: l.pn ?? (l.side === "no" ? l.a : undefined), off: l.side === "no" ? null : why },
                ];
                return (
                  <li key={l.t} data-testid="combo-leg" data-side={l.side} className="flex flex-col gap-1.5 py-2 pl-4 pr-1">
                    <div className="flex items-start gap-3">
                      <span className="min-w-0 flex-1 text-sm text-text">
                        {l.label}
                        <span className="block truncate text-xs text-text-muted">{l.game}</span>
                      </span>
                      <button type="button" onClick={() => { remove(l.t); if (n === 1) setOpen(false); }} disabled={busy}
                              aria-label={`Remove ${l.label}`}
                              className="tap inline-flex items-center justify-center text-text-muted hover:text-text">
                        ×
                      </button>
                    </div>
                    <div role="group" aria-label={`Side for ${l.label}`} data-testid="slip-flip"
                         className="inline-flex self-start rounded-inner border border-border p-0.5">
                      {sides.map((o) => {
                        const on = l.side === o.s;
                        return (
                          <button key={o.s} type="button" data-testid={`slip-side-${o.s}`} aria-pressed={on}
                                  disabled={busy || (!on && o.off !== null)} title={o.off ?? undefined}
                                  onClick={() => { if (!on) flip(l.t, o.s); }}
                                  className={`tap inline-flex items-center gap-2 rounded-inner px-3 text-xs transition-colors disabled:opacity-50 ${
                                    on ? "bg-accent text-on-accent" : "text-text-muted hover:text-text"}`}>
                            <span className="label-mono">{o.face}</span>
                            <span data-testid={on ? "slip-leg-price" : "slip-side-price"} className="font-mono text-sm tabular-nums">
                              {priceText(o.p, unit)}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                    {why && l.side === "yes" && (
                      <p data-testid="slip-no-refused" className="pr-3 text-xs text-text-muted">{why}.</p>
                    )}
                  </li>
                );
              })}
            </ul>
          </>
        )}

        <dl className="grid grid-cols-2 border-b border-border">
          <div className="border-b border-border px-4 py-3">
            <dt className="text-xs text-text-muted">{unit === "x" ? "Pays on $1, after fee" : "Costs, after fee"}</dt>
            <dd data-testid="slip-total" className="font-mono text-lg tabular-nums text-text">{totalText(total, unit)}</dd>
          </div>
          <div className="border-b border-l border-border px-4 py-3">
            <dt className="text-xs text-text-muted">Kalshi fee, once on the combo</dt>
            <dd data-testid="slip-fee" className="font-mono text-lg tabular-nums text-text">{total ? feeCents(total.fee) : "—"}</dd>
          </div>
          <div className="px-4 py-3">
            <dt className="text-xs text-text-muted">Combo price, before fee</dt>
            <dd data-testid="slip-price" className="font-mono text-lg tabular-nums text-text">
              {total ? priceText(total.price, unit) : "—"}
            </dd>
          </div>
          <div className="border-l border-border px-4 py-3">
            <dt className="text-xs text-text-muted">Games</dt>
            <dd data-testid="slip-games" className="text-sm text-text">
              {n === 0 ? "—" : games === 1 ? "Same game" : `${games} games`}
            </dd>
          </div>
        </dl>
        {n > 0 && legs.some((l) => l.a === undefined) && (
          <p className="px-4 pt-2 text-xs text-text-muted">A leg ticked on the old board has no price here: tick it again.</p>
        )}

        <div className="flex flex-col gap-2 bg-surface-2 px-4 py-4">
          <button type="button" onClick={ask} data-testid="combo-price" disabled={busy || n < MIN_LEGS}
                  className="tap w-full rounded-inner bg-accent text-base text-on-accent disabled:opacity-50">
            Price it
          </button>
          {n > 0 && n < MIN_LEGS && <p className="text-sm text-text-muted">Add one more leg to price it.</p>}
          {phase.kind === "pricing" && (
            <p role="status" data-testid="combo-pricing" className="font-mono text-sm text-text-muted">
              {pricingLabel((now - phase.since) / 1000)}
            </p>
          )}
          {phase.kind === "error" && <p role="status" data-testid="combo-error" className="text-sm text-danger">{phase.message}</p>}
          {phase.kind === "done" && phase.state.status === "answered" && (
            <QuoteView id={phase.id} quote={phase.state.quote} legs={legs} />
          )}
          {phase.kind === "done" && (phase.state.status === "refused" || phase.state.status === "failed") && (
            <p role="status" data-testid="combo-refused" data-status={phase.state.status} className="text-sm text-text">
              {phase.state.status === "refused" ? "Can't price this: " : "The pricer failed: "}{phase.state.reason}
            </p>
          )}
          <p className="flex items-center justify-between text-xs text-text-muted">
            <span>A price, not a pick. Nothing bets.</span>
            <button type="button" onClick={() => { clear(); setOpen(false); }} data-testid="combo-clear" disabled={busy || n === 0}
                    className="tap inline-flex items-center px-2 text-sm text-text-muted hover:text-text disabled:opacity-50">
              Clear slip
            </button>
          </p>
        </div>
      </div>
    </aside>
  );
}

/** Room under a page for the docked slip bar on the phone. */
export function SlipSpacer() {
  const { legs } = useBetting();
  return legs.length > 0 ? <div aria-hidden className="h-16 lg:hidden" /> : null;
}
