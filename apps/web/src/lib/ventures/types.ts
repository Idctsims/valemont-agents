// Ventures HQ (db/022) shapes and the small pure helpers the pages, the
// Home TODAY module and the unit specs share.

import { OWNER_TZ, addDays } from "@/lib/goals/period";

export const STAGES = ["idea", "building", "pre_launch", "launched", "scaling", "paused"] as const;
export type Stage = (typeof STAGES)[number];

export const STAGE_LABEL: Record<Stage, string> = {
  idea: "Idea",
  building: "Building",
  pre_launch: "Pre-launch",
  launched: "Launched",
  scaling: "Scaling",
  paused: "Paused",
};

export const WORKSTREAM_STATES = ["active", "parked", "done"] as const;
export type WorkstreamState = (typeof WORKSTREAM_STATES)[number];

export const LOG_KINDS = ["note", "decision", "milestone"] as const;
export type LogKind = (typeof LOG_KINDS)[number] | "auto";

export type Venture = {
  id: string;
  name: string;
  slug: string;
  tagline: string | null;
  role: string | null;
  stage: Stage | null;
  next_action: string | null;
  blockers: string | null;
  notes: string | null;
  sort_order: number;
  archived_at: string | null;
};

export const VENTURE_COLUMNS =
  "id, name, slug, tagline, role, stage, next_action, blockers, notes, sort_order, archived_at";

export type Workstream = {
  id: string;
  venture_id: string;
  name: string;
  state: WorkstreamState;
  next_action: string | null;
  notes: string | null;
  sort_order: number;
};

export const WORKSTREAM_COLUMNS = "id, venture_id, name, state, next_action, notes, sort_order";

export type VentureDate = {
  id: string;
  venture_id: string;
  workstream_id: string | null;
  label: string;
  due_on: string;
  done_at: string | null;
};

export const DATE_COLUMNS = "id, venture_id, workstream_id, label, due_on, done_at";

export type LogEntry = {
  id: number | string;
  kind: LogKind;
  entry: string;
  created_at: string;
  pending?: boolean;
};

/** A row of v_venture_today. */
export type TodayRow = {
  id: string;
  venture_id: string;
  venture_name: string;
  venture_slug: string;
  workstream_name: string | null;
  label: string;
  due_on: string;
  overdue: boolean;
};

/** A venture that has only a name: nothing to read yet. */
export function isSetUp(v: Pick<Venture, "stage" | "next_action" | "tagline" | "role">): boolean {
  return !!(v.stage || v.next_action || v.tagline || v.role);
}

/** "Sims & Vale Capital" -> "sims-vale-capital"; matches db/022's CHECK. */
export function slugify(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80)
    .replace(/-+$/g, "");
}

/** "Co-owner & CEO · Pre-launch", leaving out what's not set. */
export function metaLine(v: Pick<Venture, "role" | "stage">): string {
  return [v.role, v.stage ? STAGE_LABEL[v.stage] : null].filter(Boolean).join(" · ");
}

function dayDiff(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000);
}

/** "Oct 14 · 3d", "Oct 9 · today", "Oct 7 · 2d late". */
export function dueLabel(dueOn: string, today: string): string {
  const day = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(
    new Date(`${dueOn}T00:00:00Z`),
  );
  const n = dayDiff(today, dueOn);
  const rel = n === 0 ? "today" : n > 0 ? `${n}d` : `${-n}d late`;
  return `${day} · ${rel}`;
}

/** "Oct 9 · 13:42" in the owner's timezone. */
export function logStamp(iso: string, tz: string = OWNER_TZ): string {
  const d = new Date(iso);
  const day = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: tz }).format(d);
  const time = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: tz }).format(d);
  return `${day} · ${time}`;
}

/** The nearest open date of a set, or null. */
export function nearestOpen(dates: VentureDate[]): VentureDate | null {
  return (
    [...dates]
      .filter((d) => !d.done_at)
      .sort((a, b) => a.due_on.localeCompare(b.due_on))[0] ?? null
  );
}

/** A tomorrow-ish default for the "+ Date" field. */
export const defaultDue = (today: string) => addDays(today, 1);
