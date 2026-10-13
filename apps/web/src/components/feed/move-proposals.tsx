"use client";

import { useState, useTransition } from "react";

import { decideMove } from "@/app/(app)/settings/feed/actions";
import type { MoveProposal } from "@/lib/feed/data";

// "Proposed changes" on /settings/feed (db/042): moves the summarizer read in
// a story, for the owner to Confirm or Dismiss. Confirm updates who the
// person plays for (the old link ends, the new one starts today); the
// derived "his team" follow moves with it at the next rebuild. A decision is
// final; decided ones stay listed below the open ones. While a decision is in
// flight the list is aria-busy.

const KIND: Record<MoveProposal["kind"], string> = {
  trade: "Traded",
  signing: "Signed",
  release: "Released",
  retirement: "Retired",
};

function headline(p: MoveProposal): string {
  if (p.kind === "release" || p.kind === "retirement") return `${KIND[p.kind]}${p.from ? ` from ${p.from}` : ""}`;
  return `${KIND[p.kind]}${p.from ? ` from ${p.from}` : ""} to ${p.to}`;
}

function TestTag() {
  return (
    <span data-testid="test-tag" className="rounded-pill border border-text-muted px-1.5 text-text">
      test
    </span>
  );
}

export function MoveProposals({ proposals }: { proposals: MoveProposal[] }) {
  const [pending, startTransition] = useTransition();
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const open = proposals.filter((p) => p.decision === null);
  const decided = proposals.filter((p) => p.decision !== null);

  function decide(id: number, decision: "confirmed" | "dismissed") {
    setBusyId(id);
    startTransition(async () => {
      try {
        const r = await decideMove({ id, decision });
        setError(r.ok ? null : r.error);
      } catch {
        setError("That didn't save. Check the connection and try again.");
      } finally {
        setBusyId(null);
      }
    });
  }

  return (
    <section data-testid="proposals" aria-busy={pending} className="border-t border-border pt-5 pb-8">
      <h2 className="font-display text-2xl text-text">Proposed changes</h2>
      <p className="mt-1 text-sm text-text-muted text-pretty">
        Moves read in the news for people you follow. Confirm one and the feed follows them to their new team.
      </p>
      {error && (
        <p role="alert" data-testid="proposals-error" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}
      {open.length === 0 ? (
        <p data-testid="proposals-empty" className="mt-4 text-sm text-text-muted">
          Nothing to decide.
        </p>
      ) : (
        <ul className="mt-3 border-t border-border">
          {open.map((p) => (
            <li key={p.id} data-testid="proposal" data-proposal-id={p.id} className="border-b border-border py-4">
              <p className="text-base text-text">
                <span className="font-medium">{p.person}</span> · {headline(p)}
              </p>
              <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-xs text-text-muted">
                {p.isTest && <TestTag />}
                {p.storyTitle && <span className="min-w-0 truncate">{p.storyTitle}</span>}
                <span>With {p.currentWith ?? "no one"} now</span>
              </p>
              <div className="mt-3 -ml-3 flex items-center gap-1">
                <button
                  type="button"
                  data-testid="proposal-confirm"
                  disabled={pending}
                  onClick={() => decide(p.id, "confirmed")}
                  className="tap inline-flex items-center rounded-pill px-3 text-sm font-medium text-accent transition-colors hover:bg-surface-2 disabled:opacity-60"
                >
                  {busyId === p.id ? "Saving" : "Confirm"}
                </button>
                <button
                  type="button"
                  data-testid="proposal-dismiss"
                  disabled={pending}
                  onClick={() => decide(p.id, "dismissed")}
                  className="tap inline-flex items-center rounded-pill px-3 text-sm text-text-muted transition-colors hover:bg-surface-2 hover:text-text disabled:opacity-60"
                >
                  Dismiss
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {decided.length > 0 && (
        <ul data-testid="proposals-decided" className="mt-5 flex flex-col gap-2">
          {decided.map((p) => (
            <li key={p.id} data-testid="proposal-decided" className="flex flex-wrap items-baseline gap-x-2 font-mono text-xs text-text-muted">
              {p.isTest && <TestTag />}
              <span className="text-text">{p.person}</span>
              <span>{headline(p).toLowerCase()}</span>
              <span>· {p.decision}</span>
              <span>· with {p.currentWith ?? "no one"} now</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
