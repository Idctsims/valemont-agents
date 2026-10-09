"use client";

import { useState } from "react";

import { BLANK, Composer, EntryRow, ModeSection, ModeTag, type Draft } from "@/components/capital/capital-view";
import type { Entry, ModeToday } from "@/lib/capital/types";

// /design only: Capital pieces with sample data and inert handlers. Every
// state the page can be in: paper up, down and with no snapshot yet (the
// "History starts tonight" line), live, each entry kind, a saving entry, and
// the composer in paper and in the LIVE confirmation step.

const TODAY = "2026-10-09";
const noop = () => {};

const paper = (value: number, prior: number | null): ModeToday => ({
  mode: "paper",
  total: { source: "total", value, prior },
  sources: [{ source: "bankroll", value, prior }],
});

function days(n: number, from: number, step: number): { snap_date: string; value: number }[] {
  return Array.from({ length: n }, (_, i) => {
    const d = new Date(`${TODAY}T00:00:00Z`);
    d.setUTCDate(d.getUTCDate() - (n - i));
    return { snap_date: d.toISOString().slice(0, 10), value: from + step * i + (i % 3 === 0 ? -1500 : 900) };
  });
}

const ENTRIES: Entry[] = [
  { id: "e5", mode: "paper", kind: "deposit", amount: 25000, note: "Saving now", created_at: "2026-10-09T21:02:00Z", pending: true },
  { id: "e4", mode: "paper", kind: "adjustment", amount: -5000, note: "Correction: deposit +$50.00, Oct 9 · 2:41 PM", created_at: "2026-10-09T19:44:00Z" },
  { id: "e3", mode: "paper", kind: "withdrawal", amount: 4000, note: null, created_at: "2026-10-09T19:41:00Z" },
  { id: "e2", mode: "live", kind: "deposit", amount: 50000, note: "First live dollars", created_at: "2026-10-08T15:00:00Z" },
  { id: "e1", mode: "paper", kind: "deposit", amount: 100000, note: "Paper bankroll seed", created_at: "2026-10-08T03:00:00Z" },
];

function ComposerSample({ start }: { start: Draft }) {
  const [draft, setDraft] = useState(start);
  return <Composer ref={null} draft={draft} onChange={setDraft} onSubmit={noop} />;
}

export function CapitalHeroSamples() {
  return (
    <div className="flex flex-col gap-10">
      <div>
        <p className="mb-3 text-sm text-text-muted">Paper, up today, with history</p>
        <ModeSection mode={paper(125000, 100000)} snapshots={days(40, 80000, 500)} today={TODAY} saving={false} hero />
      </div>
      <div>
        <p className="mb-3 text-sm text-text-muted">Paper, down today, saving</p>
        <ModeSection mode={paper(96000, 100000)} snapshots={days(6, 99000, 200)} today={TODAY} saving hero />
      </div>
      <div>
        <p className="mb-3 text-sm text-text-muted">Paper, first day: no snapshot yet</p>
        <ModeSection mode={paper(100000, null)} snapshots={[]} today={TODAY} saving={false} hero />
      </div>
      <div>
        <p className="mb-3 text-sm text-text-muted">Live: only when live rows exist; its own total, change and chart</p>
        <ModeSection
          mode={{ mode: "live", total: { source: "total", value: 55000, prior: 50000 }, sources: [{ source: "bankroll", value: 55000, prior: 50000 }] }}
          snapshots={days(5, 49000, 300)}
          today={TODAY}
          saving={false}
        />
      </div>
    </div>
  );
}

export function CapitalTagSamples() {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <ModeTag mode="paper" />
      <ModeTag mode="live" />
    </div>
  );
}

export function CapitalEntrySamples() {
  return (
    <ul className="border-t border-border">
      {ENTRIES.map((e) => (
        <EntryRow key={e.id} entry={e} showMode onCorrect={noop} />
      ))}
    </ul>
  );
}

export function CapitalComposerSamples() {
  return (
    <div className="flex flex-col gap-8">
      <div>
        <p className="text-sm text-text-muted">Paper, filled</p>
        <ComposerSample start={{ ...BLANK, amount: "250.00", note: "Weekly top-up" }} />
      </div>
      <div>
        <p className="text-sm text-text-muted">Correct with adjustment, pre-filled</p>
        <ComposerSample start={{ ...BLANK, kind: "adjustment", sign: "subtract", amount: "50.00", note: "Correction: deposit +$50.00, Oct 9 · 2:41 PM" }} />
      </div>
      <div>
        <p className="text-sm text-text-muted">Live: the confirmation step before anything can be added</p>
        <ComposerSample start={{ ...BLANK, mode: "live", amount: "100.00" }} />
      </div>
    </div>
  );
}
