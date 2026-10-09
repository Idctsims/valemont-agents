"use client";

import { Fold } from "@/components/ui/fold";
import { TodayItem } from "@/components/ventures/today";
import { LogRow, WorkstreamRow } from "@/components/ventures/venture-detail";
import type { LogEntry, TodayRow, VentureDate, Workstream } from "@/lib/ventures/types";

// /design only: venture pieces with sample data and inert handlers.

const TODAY = "2026-10-09";
const noop = () => {};

const WORKSTREAMS: Workstream[] = [
  { id: "w1", venture_id: "v", name: "Permitting", state: "active", next_action: "Submit DEP application", notes: "Pre-app meeting held.", sort_order: 1 },
  { id: "w2", venture_id: "v", name: "Vessel renders", state: "active", next_action: "Find a renderer", notes: null, sort_order: 2 },
  { id: "w3", venture_id: "v", name: "Legal", state: "parked", next_action: "SPAs in draft; parked until investment stage", notes: null, sort_order: 3 },
];

const DATES: VentureDate[] = [
  { id: "d1", venture_id: "v", workstream_id: "w1", label: "DEP application due", due_on: "2026-10-14", done_at: null },
  { id: "d2", venture_id: "v", workstream_id: "w1", label: "Pre-app meeting", due_on: "2026-10-02", done_at: "2026-10-02T15:00:00Z" },
];

const LOG: LogEntry[] = [
  { id: 4, kind: "auto", entry: "Next action: Find a renderer → Brief two renderers", created_at: "2026-10-09T18:42:00Z" },
  { id: 3, kind: "decision", entry: "Go with a Florida OpCo under the Delaware holding company.", created_at: "2026-10-08T15:10:00Z" },
  { id: 2, kind: "milestone", entry: "Pre-app meeting with DEP done.", created_at: "2026-10-02T16:00:00Z" },
  { id: 1, kind: "note", entry: "Kendrick knows two marina operators worth meeting.", created_at: "2026-10-01T13:05:00Z" },
];

const TODAY_ROWS: TodayRow[] = [
  { id: "t1", venture_id: "v", venture_name: "Sail Beach Club", venture_slug: "sail-beach-club", workstream_name: "Permitting", label: "DEP application due", due_on: TODAY, overdue: false },
  { id: "t2", venture_id: "v", venture_name: "Clipd", venture_slug: "clipd", workstream_name: null, label: "Send revised term sheet", due_on: "2026-10-07", overdue: true },
];

export function WorkstreamSamples() {
  return (
    <>
      <ul className="border-t border-border">
        {WORKSTREAMS.filter((w) => w.state !== "parked").map((w) => (
          <WorkstreamRow
            key={w.id}
            ws={w}
            dates={DATES.filter((d) => d.workstream_id === w.id)}
            today={TODAY}
            onEdit={noop}
            onToggleDate={noop}
            onAddDate={noop}
          />
        ))}
      </ul>
      <Fold label="Parked" count={1} defaultOpen>
        {WORKSTREAMS.filter((w) => w.state === "parked").map((w) => (
          <WorkstreamRow key={w.id} ws={w} dates={[]} today={TODAY} onEdit={noop} onToggleDate={noop} onAddDate={noop} />
        ))}
      </Fold>
    </>
  );
}

export function LogSamples() {
  return (
    <ul className="border-t border-border">
      {LOG.map((l) => (
        <LogRow key={l.id} entry={l} />
      ))}
    </ul>
  );
}

export function TodaySamples() {
  return (
    <ul className="border-t border-border">
      {TODAY_ROWS.map((r) => (
        <TodayItem key={r.id} row={r} onDone={noop} />
      ))}
    </ul>
  );
}
