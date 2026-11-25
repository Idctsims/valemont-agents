"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { parlayDetails } from "@/app/(app)/betting/parlays/actions";
import { useBetting } from "@/components/betting/betting-store";
import { clock } from "@/components/betting/parlay-card";
import { useWags } from "@/components/wags/wags-provider";
import type { ParlayDetails } from "@/lib/betting/parlay-data";
import { TIER_NAME, cohortName, perDollar, signedUsd, splitLabel, usd } from "@/lib/betting/parlays";

// A ticket's Details (docs/parlays_page.md §3): the paper stake and payout,
// how it was priced (the model's chance, the price paid as a chance, what was
// paid over the model, the expected quote pending M1), the CLV (proxy) with
// its close ages, labelled not profit, the cohort, and the selection log as
// recorded (verbatim, cents and all: it is the record). "Copy legs to the
// slip" puts the legs on the Board's slip so the owner can build the ticket
// on Kalshi by hand; nothing here bets (CLAUDE.md §1).

function pct(x: number | undefined, digits = 1): string {
  return typeof x === "number" ? `${(x * 100).toFixed(digits)}%` : "—";
}
function signedPct(x: number | undefined): string {
  return typeof x === "number" ? `${x < 0 ? "−" : "+"}${Math.abs(x * 100).toFixed(1)}%` : "—";
}

function Row({ k, v, muted = false, testId }: { k: React.ReactNode; v: React.ReactNode; muted?: boolean; testId?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-border py-2.5">
      <dt className="text-sm text-text-muted">{k}</dt>
      <dd data-testid={testId} className={`text-right font-mono text-sm tabular-nums ${muted ? "text-text-muted" : "text-text"}`}>{v}</dd>
    </div>
  );
}

function Fact({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="rounded-inner bg-surface-2 px-3 py-2.5">
      <dt className="text-xs text-text-muted">{k}</dt>
      <dd className="mt-0.5 font-semibold text-text">{v}</dd>
    </div>
  );
}

export function TicketDetails({ id, started, initial }: {
  id: number;
  started: boolean;
  /** Already read (no fetch). */
  initial?: ParlayDetails;
}) {
  const [state, setState] = useState<{ id: number; t?: ParlayDetails; error?: string } | null>(
    initial ? { id, t: initial } : null);
  const [copied, setCopied] = useState(false);
  const { put } = useBetting();
  const wags = useWags();

  useEffect(() => {
    if (initial) return;
    let live = true;
    parlayDetails(id).then((res) => {
      if (live) setState(res.ok ? { id, t: res.ticket } : { id, error: res.error });
    });
    return () => { live = false; };
  }, [id, initial]);

  if (!state || state.id !== id) return <p className="py-8 text-center text-sm text-text-muted" aria-busy="true">Opening…</p>;
  if (!state.t) return <p role="alert" className="py-8 text-center text-sm text-danger">{state.error}</p>;
  const t = state.t;
  const where = t.scope === "cross_sport" ? "Cross-sport" : t.sport ?? "";
  const mult = t.stake > 0 ? t.pays / t.stake : 0;

  const copy = () => {
    for (const l of t.legs) {
      const [label, game] = splitLabel(l.label ?? l.m);
      put({ t: l.m, side: l.side, label, game: game ?? "", sport: t.sport });
    }
    setCopied(true);
  };

  return (
    <div data-testid="parlay-details" data-id={t.id}>
      {/* pr-10: the sheet's close button sits in this corner on the phone. */}
      <div className="flex items-start justify-between gap-3 pr-10 lg:pr-0">
        <div>
          <p className="label-mono text-text-muted">{TIER_NAME[t.tier]} · {where} · {clock(t.at)} slot</p>
          <p className="mt-1 font-display text-4xl text-text">×{mult.toFixed(2)}</p>
        </div>
        <span className="label-mono rounded-tile bg-paper-bg px-1.5 text-paper-fg">PAPER</span>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-2">
        <Fact k="Paper stake" v={usd(t.stake)} />
        <Fact k="Pays if all hit" v={usd(t.pays)} />
        <Fact k="Legs" v={t.legs.length} />
        <Fact k="On Kalshi" v={t.combo === "fits" ? "Fits as one combo" : "Build it by hand"} />
      </dl>

      {t.outcome && (
        <dl className="mt-3">
          <Row k="Result" testId="details-result"
               v={t.outcome === "miss" ? `Missed · ${signedUsd(-t.stake)}`
                 : `${t.outcome === "hit" ? "Hit" : "Partial"} · paid ${usd(t.paid ?? 0)} · ${signedUsd((t.paid ?? 0) - t.stake)}`} />
          {typeof t.pnl === "number" && <Row k="Return" v={perDollar(t.pnl)} />}
        </dl>
      )}

      {!started && !t.outcome && (
        <div className="mt-3">
          <button type="button" onClick={copy} data-testid="parlay-copy"
                  className="tap w-full rounded-inner bg-accent px-4 font-semibold text-on-accent">
            {copied ? "Copied to the slip" : "Copy legs to the slip"}
          </button>
          {copied && <p className="mt-1.5 text-sm text-text-muted">Build it on Kalshi from the slip on the{" "}
            <Link href="/betting" className="font-semibold text-accent-ink">Board</Link>.</p>}
        </div>
      )}

      <h3 className="label-mono mt-5 text-text-muted">How it was priced</h3>
      <dl>
        <Row k={`The model's chance all ${t.legs.length} hit`} v={pct(t.fair)} testId="details-fair" />
        <Row k="Price paid, as a chance" v={pct(t.scored)} />
        <Row k="Paid over the model" v={signedPct(t.rel)} />
        <Row k="Fee, inside the stake" v={usd(t.fee * t.pays)} />
        <Row k="Expected combo quote" v={t.eq ?? "pending (M1)"} muted />
        {!!t.prior && <Row k="Pairs on a prior correlation" v={`${t.prior} of ${t.pairs}`} muted />}
      </dl>

      <h3 className="label-mono mt-5 flex items-center gap-2 text-text-muted">
        Closing line <span className="rounded-tile border border-border px-1.5">not profit</span>
      </h3>
      <dl>
        {t.close?.status === "captured" ? (
          <>
            <Row k="CLV (proxy)" testId="details-clv" v={`${signedPct(t.close.clv_pct)} (${t.close.clv! >= 0 ? "+" : "−"}${Math.abs(t.close.clv ?? 0).toFixed(4)})`} />
            <Row k="Close ages" testId="details-ages"
                 v={t.close.age_med != null ? `median ${t.close.age_med} min · max ${t.close.age_max} min` : "not recorded"} />
          </>
        ) : t.close?.status === "missed" ? (
          <Row k="CLV (proxy)" v={`missed: ${(t.close.reason ?? "").replace(/^unavailable: /, "")}`} muted />
        ) : (
          <Row k="CLV (proxy)" v="read after the last game starts" muted />
        )}
      </dl>

      <details className="mt-3" data-testid="parlay-log">
        <summary className="tap flex items-center font-semibold text-accent-ink">Why these legs: the selection log, as recorded</summary>
        <ol className="mt-1 flex flex-col gap-2">
          {t.reasons.map((r, i) => <li key={i} className="font-mono text-xs leading-relaxed text-text-muted">{r}</li>)}
        </ol>
      </details>

      <button type="button" data-testid="parlay-ask-wags"
              onClick={() => wags.open({ label: `Parlays · ${TIER_NAME[t.tier]} ${where}`, pillar: "parlays" })}
              className="tap mt-3 text-sm font-semibold text-accent-ink">
        Ask Wags about the parlays
      </button>

      <p className="mt-4 text-xs text-text-muted">
        {cohortName(t.rv)}. Committed {clock(t.at)}, before any game started, and never edited.
        {t.fill ? ` ${t.fill[0].toUpperCase()}${t.fill.slice(1)}: the only cross-game fill sample is 2025 NFL.` : ""}
      </p>
    </div>
  );
}
