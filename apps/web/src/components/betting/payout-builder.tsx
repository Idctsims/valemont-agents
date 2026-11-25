"use client";

import { X } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";

import { askPayout, payoutStatus } from "@/app/(app)/betting/actions";
import { useBetting } from "@/components/betting/betting-store";
import {
  chanceWords, dollars, expText, parseAmounts, ticketLegs, warns, type PayoutState, type PayoutTicket,
} from "@/lib/betting/payout";
import { cents, rowWords } from "@/lib/betting/singles";

// "Bet $X to win $Y" (E1; owner, Nov 25; docs/payout_target.md): a sheet on
// the Props tab. The worker (db/094's build_payout job) answers with up to 3
// tickets that pay back at least the target (or the nearest it can reach),
// each with its chance to hit and its expected return; Load into slip puts one
// in the slip, whose Price it re-quotes it live. A suggestion: nothing bets.

const POLL_MS = 1500;
const GIVE_UP_S = 90;

type Phase =
  | { kind: "idle" }
  | { kind: "asking" }
  | { kind: "building"; id: number; since: number; stake: number; payout: number }
  | { kind: "done"; state: PayoutState; stake: number; payout: number }
  | { kind: "error"; message: string };

function Ticket({ t, stake, onLoad }: { t: PayoutTicket; stake: number; onLoad: () => void }) {
  return (
    <li data-testid="payout-ticket" data-legs={t.k} className="rounded-inner border border-border bg-surface p-4">
      <div className="flex items-baseline justify-between gap-3">
        <p className="font-semibold text-text">
          Pays <span data-testid="payout-pays">{dollars(t.contracts)}</span> on {dollars(stake)}
        </p>
        <p className="label-mono text-text-muted">{t.k} {t.k === 1 ? "bet" : "legs"}</p>
      </div>
      <p className="mt-1 text-sm text-text">
        Chance to hit: <span data-testid="payout-chance">{chanceWords(t.fair)}</span>{" "}
        <span className="text-text-muted">({(t.fair * 100).toFixed(1)}%)</span>
      </p>
      <p className="text-sm text-text-muted">
        Expected return <span data-testid="payout-exp">{expText(t.exp)}</span> of the stake, after the fee
        {t.combo !== "fits" && <> · <span className="text-on-pace">{t.combo_reason ?? "won't go in as one Kalshi combo"}</span></>}
        {t.k === 1 && " · a single bet, no parlay needed"}
      </p>
      <ul className="mt-3 border-t border-border">
        {t.legs.map((l) => {
          const w = rowWords({ ...l, fee: 0, mult: 0, combo: l.yo ? "yes_only" : undefined });
          return (
            <li key={`${l.m}-${l.side}`} className="flex items-start justify-between gap-3 border-b border-border py-2 text-sm">
              <span className="min-w-0">
                <span className="block truncate font-semibold text-text">{w.title}{w.detail && w.game ? `: ${w.detail}` : ""}</span>
                <span className="block truncate text-xs text-text-muted">{w.game ?? w.detail}</span>
              </span>
              <span className="shrink-0 font-mono text-xs tabular-nums text-text-muted">
                {cents(l.ask).replace("+", "")}{l.tier !== "kalshi" && ` · ${l.tier === "books" ? "books" : "ESPN"}`}
              </span>
            </li>
          );
        })}
      </ul>
      <button type="button" data-testid="payout-load" onClick={onLoad}
              className="tap mt-3 inline-flex w-full items-center justify-center rounded-pill bg-accent px-4 font-semibold text-on-accent">
        Load into slip
      </button>
    </li>
  );
}

/** Ask the worker for tickets and poll for the answer (the slip's usePricing, for payouts). */
function usePayout() {
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [now, setNow] = useState(() => Date.now());

  const building = phase.kind === "building" ? phase : null;
  useEffect(() => {
    if (!building) return;
    let stop = false;
    const tick = setInterval(() => setNow(Date.now()), 500);
    const poll = async () => {
      while (!stop) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        if (stop) return;
        const res = await payoutStatus(building.id);
        if (stop) return;
        if (!res.ok) { setPhase({ kind: "error", message: res.error }); return; }
        if (res.state.status !== "queued") {
          setPhase({ kind: "done", state: res.state, stake: building.stake, payout: building.payout });
          return;
        }
        if ((Date.now() - building.since) / 1000 > GIVE_UP_S) {
          setPhase({ kind: "error", message: "Still building after 90 seconds. The worker may be down; try again later." });
          return;
        }
      }
    };
    void poll();
    return () => { stop = true; clearInterval(tick); };
  }, [building]);

  const build = async (stake: number, payout: number) => {
    setPhase({ kind: "asking" });
    const res = await askPayout(stake, payout);
    if (!res.ok) { setPhase({ kind: "error", message: res.error }); return; }
    const since = Date.now();
    setNow(since);
    setPhase({ kind: "building", id: res.id, since, stake, payout });
  };

  return { phase, now, build, busy: phase.kind === "asking" || phase.kind === "building" };
}

export function PayoutBuilder() {
  const { load } = useBetting();
  const sheet = useRef<HTMLDialogElement>(null);
  const [stake, setStake] = useState("10");
  const [payout, setPayout] = useState("100");
  const { phase, now, build, busy } = usePayout();
  const amounts = parseAmounts(stake, payout);
  const ok = !("error" in amounts);
  const building = phase.kind === "building" ? phase : null;

  const field = "min-h-11 w-full min-w-0 rounded-inner border border-border bg-surface px-3 font-mono text-base text-text";

  return (
    <>
      <button type="button" data-testid="payout-open" onClick={() => sheet.current?.showModal()}
              className="tap flex items-center font-semibold text-accent-ink">
        Build to a payout
      </button>
      <dialog ref={sheet} aria-label="Build to a payout" data-testid="payout-sheet"
              onClick={(e) => { if (e.target === sheet.current) sheet.current?.close(); }}
              className="m-0 mt-auto w-full max-w-none bg-transparent p-0 text-text backdrop:bg-bg/80 backdrop:backdrop-blur-sm lg:mx-auto lg:mt-16 lg:max-w-xl">
        <div className="relative max-h-tool overflow-y-auto rounded-t-card border border-border bg-surface-2 px-5 pb-8 pt-3 lg:rounded-card">
          <span aria-hidden className="mx-auto mb-3 block h-1.5 w-10 rounded-pill bg-surface-3 lg:hidden" />
          <button type="button" onClick={() => sheet.current?.close()} aria-label="Close"
                  className="tap absolute right-2 top-2 inline-flex size-10 items-center justify-center rounded-pill text-text-muted hover:text-text">
            <X size={20} aria-hidden />
          </button>
          <h2 className="font-display text-2xl text-text">Build to a payout</h2>
          <p className="mt-1 max-w-measure text-sm text-text-muted">
            Your stake and what you want back. It builds the slips from everything on the board with the best chance
            of paying at least that, and Price it then quotes the one you load. Nothing is guaranteed.
          </p>
          <form className="mt-4 grid grid-cols-2 gap-3" aria-busy={busy}
                onSubmit={(e) => { e.preventDefault(); if (ok) void build(amounts.stake, amounts.payout); }}>
            <label className="flex flex-col gap-1 text-sm text-text-muted">
              Stake $
              <input inputMode="decimal" value={stake} onChange={(e) => setStake(e.target.value)} data-testid="payout-stake"
                     className={field} />
            </label>
            <label className="flex flex-col gap-1 text-sm text-text-muted">
              Pays back $
              <input inputMode="decimal" value={payout} onChange={(e) => setPayout(e.target.value)} data-testid="payout-back"
                     className={field} />
            </label>
            <p className="col-span-2 text-sm text-text-muted" data-testid="payout-multiple">
              {ok ? <>{(amounts.payout / amounts.stake).toFixed(amounts.payout / amounts.stake < 10 ? 1 : 0)}× your stake</> : amounts.error}
            </p>
            {ok && warns(amounts.stake, amounts.payout) && (
              <p role="note" data-testid="payout-warn" className="col-span-2 text-sm font-semibold text-on-pace">
                This is the longshot corner. Tickets this cheap lose the most, on average, of anything on Kalshi.
              </p>
            )}
            <button type="submit" disabled={!ok || busy} data-testid="payout-build"
                    className="tap col-span-2 inline-flex items-center justify-center rounded-pill bg-accent px-4 font-semibold text-on-accent disabled:opacity-60">
              {phase.kind === "asking" ? "Asking…" : building ? `Building… ${Math.round((now - building.since) / 1000)} s` : "Build"}
            </button>
          </form>

          {phase.kind === "error" && <p role="alert" className="mt-4 text-sm text-danger">{phase.message}</p>}
          {phase.kind === "done" && phase.state.status === "failed" && (
            <p role="alert" className="mt-4 text-sm text-danger">{phase.state.reason}</p>
          )}
          {phase.kind === "done" && phase.state.status === "answered" && (() => {
            const a = phase.state.answer;
            return (
              <section data-testid="payout-answer" data-nearest={a.nearest ? "1" : "0"} className="mt-5">
                <p className="text-sm text-text">
                  {a.tickets.length === 0
                    ? "Nothing on the board can be built right now."
                    : a.nearest
                      ? `Can't reach ${dollars(phase.payout)} on ${dollars(phase.stake)} here. The closest it can build:`
                      : `${a.tickets.length === 1 ? "A slip" : `${a.tickets.length} slips`} paying at least ${dollars(phase.payout)}, best chance first:`}
                </p>
                <ul className="mt-3 flex flex-col gap-3">
                  {a.tickets.map((t, i) => (
                    <Ticket key={i} t={t} stake={phase.stake}
                            onLoad={() => { load(ticketLegs(t)); sheet.current?.close(); }} />
                  ))}
                </ul>
                <p className="mt-3 text-xs text-text-muted">
                  A suggestion, not a pick. Prices are the board&apos;s from the last 20 minutes; Kalshi prices the combo
                  again when you ask. Nothing here bets.
                </p>
              </section>
            );
          })()}
        </div>
      </dialog>
    </>
  );
}
