"use client";

import { useEffect, useRef, useState } from "react";

import { comboStatus, priceCombo } from "@/app/(app)/betting/actions";
import { useWags } from "@/components/wags/wags-provider";
import {
  MIN_LEGS, ageLabel, comboStatusLabel, costLabel, legTierLabel, pricesStale, pricingLabel, probLabel, quotedLegs,
  ratioLabel, seLabel,
  type ComboLeg, type ComboState, type Quote,
} from "@/lib/betting/combo";

// The combo sheet (Chat 8 Phase 3; db/076): the ticked legs, then the price.
// It asks the worker (a combo_requests row; the price_combo job answers on its
// next poll) and polls for the answer, showing the time it has waited. The
// quote: the naive product, the correlated fair p (± its Monte Carlo SE), the
// ratio, Kalshi's naive cost, whether Kalshi takes it as one combo, and each
// leg's quoted p and tier; a "prior" chip when any pair's rho is a prior or
// borrowed (not measured), and "unmodeled pair" when a pair had no row (0).
// Read-only: nothing here bets (CLAUDE.md §1).

const POLL_MS = 1500;
const GIVE_UP_S = 120;

type Phase =
  | { kind: "idle" }
  | { kind: "asking" }
  | { kind: "pricing"; id: number; since: number }
  | { kind: "done"; id: number; state: ComboState }
  | { kind: "error"; message: string };

function Chip({ children, testId, title }: { children: React.ReactNode; testId: string; title?: string }) {
  return (
    <span data-testid={testId} title={title}
          className="label-mono inline-flex h-6 items-center whitespace-nowrap rounded-pill border border-border px-2 text-text-muted">
      {children}
    </span>
  );
}

function QuoteView({ id, quote, legs }: { id: number; quote: Quote; legs: readonly ComboLeg[] }) {
  const wags = useWags();
  const quoted = quotedLegs(quote);
  const mc = quote.blocks.some((b) => b.method === "monte_carlo");
  return (
    <div data-testid="combo-quote" data-status={quote.combo_status} className="mt-3">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-sm tabular-nums">
        <dt className="text-text-muted">Naive</dt>
        <dd data-testid="combo-naive" className="text-right text-text">{probLabel(quote.naive_p)}</dd>
        <dt className="text-text-muted">Fair (correlated)</dt>
        <dd data-testid="combo-fair" className="text-right text-text">
          {probLabel(quote.fair_p)}
          {mc && <span data-testid="combo-se" className="ml-1 text-text-muted">{seLabel(quote.se)}</span>}
        </dd>
        <dt className="text-text-muted">Fair / naive</dt>
        <dd data-testid="combo-ratio" className="text-right text-text">{ratioLabel(quote)}</dd>
        <dt className="text-text-muted">Kalshi naive cost</dt>
        <dd data-testid="combo-cost" className="text-right text-text">{costLabel(quote.kalshi_naive_cost)}</dd>
      </dl>
      <p data-testid="combo-status" className="mt-2 text-sm text-text">{comboStatusLabel(quote)}</p>
      <p className="mt-2 flex flex-wrap gap-2">
        {pricesStale(quote) && (
          <Chip testId="combo-prices-stale" title="A leg's Kalshi price is over 20 minutes old. Still priced; re-price before acting on it.">
            prices stale
          </Chip>
        )}
        {quote.prior_pairs > 0 && (
          <Chip testId="combo-prior" title="At least one correlation here is a prior or borrowed from another market, not a measurement.">
            prior
          </Chip>
        )}
        {quote.unmodeled_pairs > 0 && (
          <Chip testId="combo-unmodeled" title="No correlation row for this pair: priced as independent (0).">
            unmodeled pair{quote.unmodeled_pairs > 1 ? ` ×${quote.unmodeled_pairs}` : ""}
          </Chip>
        )}
      </p>
      <ul className="mt-2 divide-y divide-border border-t border-border">
        {legs.map((l) => {
          const q = quoted.get(l.t);
          return (
            <li key={l.t} data-testid="combo-quoted-leg" className="flex flex-wrap items-baseline justify-between gap-x-3 py-1.5 text-sm">
              <span className="min-w-0 flex-1 text-text">
                {l.side === "no" ? "NOT " : ""}{l.label}
              </span>
              <span className="font-mono tabular-nums text-text-muted">{q ? probLabel(q.p) : "—"}</span>
              {q && <Chip testId="combo-leg-tier">{legTierLabel(q.tier)}</Chip>}
              {q && (
                <span data-testid="combo-leg-age" data-stale={q.stale_k ? "1" : "0"}
                      className={`label-mono basis-full text-right ${q.stale_k ? "text-text" : "text-text-muted"}`}>
                  {ageLabel(q.age_s)}{q.stale_k ? " · stale" : ""}
                </span>
              )}
            </li>
          );
        })}
      </ul>
      <p className="mt-2 text-xs text-text-muted">
        A fair price is not a profit or a pick. Nothing is ever bet automatically.
      </p>
      <button type="button" data-testid="combo-ask-wags"
              onClick={() => wags.open({ label: `Combo · #${id}`, pillar: "combos", slug: String(id) })}
              className="tap mt-2 inline-flex items-center rounded-pill border border-border px-3 text-sm text-text hover:border-accent hover:text-accent">
        Ask Wags about this
      </button>
    </div>
  );
}

export function ComboSheet({ legs, onRemove, onClear }: {
  legs: readonly ComboLeg[];
  onRemove: (ticker: string) => void;
  onClear: () => void;
}) {
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [now, setNow] = useState(() => Date.now());
  const [open, setOpen] = useState(true);
  const key = legs.map((l) => `${l.t}:${l.side}`).join("|");
  const asked = useRef<string | null>(null);

  // A different set of legs makes the last price stale.
  useEffect(() => {
    if (asked.current !== null && asked.current !== key) {
      asked.current = null;
      setPhase({ kind: "idle" });
    }
  }, [key]);

  const pricing = phase.kind === "pricing" ? phase : null;
  useEffect(() => {
    if (!pricing) return;
    let stop = false;
    const tick = setInterval(() => setNow(Date.now()), 500);
    const poll = async () => {
      while (!stop) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        if (stop) return;
        const res = await comboStatus(pricing.id);
        if (stop) return;
        if (!res.ok) {
          setPhase({ kind: "error", message: res.error });
          return;
        }
        if (res.state.status !== "queued") {
          setPhase({ kind: "done", id: pricing.id, state: res.state });
          return;
        }
        if ((Date.now() - pricing.since) / 1000 > GIVE_UP_S) {
          setPhase({ kind: "error", message: "Still pricing after 2 minutes. The worker may be down; try again later." });
          return;
        }
      }
    };
    void poll();
    return () => {
      stop = true;
      clearInterval(tick);
    };
  }, [pricing]);

  if (legs.length === 0) return null;

  const ask = async () => {
    asked.current = key;
    setPhase({ kind: "asking" });
    const res = await priceCombo(legs.map((l) => ({ t: l.t, side: l.side })));
    if (!res.ok) {
      setPhase({ kind: "error", message: res.error });
      return;
    }
    const since = Date.now();
    setNow(since);
    setPhase({ kind: "pricing", id: res.id, since });
  };

  const busy = phase.kind === "asking" || phase.kind === "pricing";
  return (
    <section data-testid="combo-sheet" aria-label="Combo" aria-busy={busy}
             className="fixed inset-x-0 bottom-dock z-30 border-t border-border bg-surface px-safe py-3 lg:bottom-0 lg:pl-rail">
      <div className="mx-auto max-h-96 overflow-y-auto lg:max-w-3xl lg:px-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} data-testid="combo-sheet-toggle"
                  className="tap inline-flex items-center text-base text-text">
            Combo · {legs.length} {legs.length === 1 ? "leg" : "legs"}
          </button>
          <span className="flex items-center gap-2">
            <button type="button" onClick={onClear} data-testid="combo-clear" disabled={busy}
                    className="tap inline-flex items-center px-2 text-sm text-text-muted hover:text-text">
              Clear
            </button>
            <button type="button" onClick={ask} data-testid="combo-price" disabled={busy || legs.length < MIN_LEGS}
                    className="tap inline-flex items-center rounded-pill bg-accent px-4 text-sm text-on-accent disabled:opacity-50">
              Price it
            </button>
          </span>
        </div>
        {open && (
          <>
            {phase.kind !== "done" && (
              <ul data-testid="combo-legs" className="mt-2 divide-y divide-border border-t border-border">
                {legs.map((l) => (
                  <li key={l.t} data-testid="combo-leg" data-side={l.side} className="flex items-center justify-between gap-3 py-1">
                    <span className="min-w-0 flex-1 text-sm text-text">
                      <span className="label-mono mr-2 text-accent">{l.side === "yes" ? "YES" : "NO"}</span>
                      {l.label}
                      <span className="block text-xs text-text-muted">{l.game}</span>
                    </span>
                    <button type="button" onClick={() => onRemove(l.t)} disabled={busy} aria-label={`Remove ${l.label}`}
                            className="tap inline-flex items-center px-2 text-text-muted hover:text-text">
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {legs.length < MIN_LEGS && <p className="mt-2 text-sm text-text-muted">Tick one more leg to price it.</p>}
            {phase.kind === "pricing" && (
              <p role="status" data-testid="combo-pricing" className="mt-2 font-mono text-sm text-text-muted">
                {pricingLabel((now - phase.since) / 1000)}
              </p>
            )}
            {phase.kind === "error" && <p role="status" data-testid="combo-error" className="mt-2 text-sm text-danger">{phase.message}</p>}
            {phase.kind === "done" && phase.state.status === "answered" && (
              <QuoteView id={phase.id} quote={phase.state.quote} legs={legs} />
            )}
            {phase.kind === "done" && (phase.state.status === "refused" || phase.state.status === "failed") && (
              <p role="status" data-testid="combo-refused" data-status={phase.state.status} className="mt-2 text-sm text-text">
                {phase.state.status === "refused" ? "Can't price this: " : "The pricer failed: "}{phase.state.reason}
              </p>
            )}
          </>
        )}
      </div>
    </section>
  );
}
