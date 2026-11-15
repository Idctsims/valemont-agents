"use client";

import { useState } from "react";

import { QuoteView, usePricing } from "@/components/betting/combo-quote";
import { MIN_LEGS, pricingLabel, type ComboLeg } from "@/lib/betting/combo";

// The Phase 3 combo sheet, kept only for /betting/classic until the new page
// passes its checks (Workstream B, Step 1); then this file goes with it. The
// pricing itself lives in combo-quote.tsx, shared with the new slip. Its test
// ids moved to the slip, so none are set here.

export function ComboSheet({ legs, onRemove, onClear }: {
  legs: readonly ComboLeg[];
  onRemove: (ticker: string) => void;
  onClear: () => void;
}) {
  const { phase, now, ask, busy } = usePricing(legs);
  const [open, setOpen] = useState(true);
  if (legs.length === 0) return null;

  return (
    <section aria-label="Combo" aria-busy={busy}
             className="fixed inset-x-0 bottom-dock z-30 border-t border-border bg-surface px-safe py-3 lg:bottom-0 lg:pl-rail">
      <div className="mx-auto max-h-96 overflow-y-auto lg:max-w-3xl lg:px-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
                  className="tap inline-flex items-center text-base text-text">
            Combo · {legs.length} {legs.length === 1 ? "leg" : "legs"}
          </button>
          <span className="flex items-center gap-2">
            <button type="button" onClick={onClear} disabled={busy}
                    className="tap inline-flex items-center px-2 text-sm text-text-muted hover:text-text">
              Clear
            </button>
            <button type="button" onClick={ask} disabled={busy || legs.length < MIN_LEGS}
                    className="tap inline-flex items-center rounded-pill bg-accent px-4 text-sm text-on-accent disabled:opacity-50">
              Price it
            </button>
          </span>
        </div>
        {open && (
          <>
            {phase.kind !== "done" && (
              <ul className="mt-2 divide-y divide-border border-t border-border">
                {legs.map((l) => (
                  <li key={l.t} className="flex items-center justify-between gap-3 py-1">
                    <span className="min-w-0 flex-1 text-sm text-text">
                      <span className="label-mono mr-2 text-accent-ink">{l.side === "yes" ? "YES" : "NO"}</span>
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
              <p role="status" className="mt-2 font-mono text-sm text-text-muted">{pricingLabel((now - phase.since) / 1000)}</p>
            )}
            {phase.kind === "error" && <p role="status" className="mt-2 text-sm text-danger">{phase.message}</p>}
            {phase.kind === "done" && phase.state.status === "answered" && (
              <QuoteView id={phase.id} quote={phase.state.quote} legs={legs} />
            )}
            {phase.kind === "done" && (phase.state.status === "refused" || phase.state.status === "failed") && (
              <p role="status" className="mt-2 text-sm text-text">
                {phase.state.status === "refused" ? "Can't price this: " : "The pricer failed: "}{phase.state.reason}
              </p>
            )}
          </>
        )}
      </div>
    </section>
  );
}
