import { WEEKLY_CAP, type Area, type Horizon } from "./period";

export type GoalStatus = "open" | "done" | "dropped";

export type Goal = {
  id: string;
  title: string;
  notes: string | null;
  horizon: Horizon;
  area: Area | null;
  period_start: string | null;
  status: GoalStatus;
  carried_from: string | null;
  carry_count: number;
  sort_order: number;
  created_at: string;
  completed_at: string | null;
  /** Carried on to a later period (a child row exists). Read-only here. */
  moved: boolean;
  /** Optimistic row not yet confirmed by the server. */
  pending?: boolean;
};

export const GOAL_COLUMNS =
  "id, title, notes, horizon, area, period_start, status, carried_from, carry_count, sort_order, created_at, completed_at";

/** The state a row is drawn in. */
export type RowState = "open" | "done" | "dropped" | "moved";

export function rowState(g: Goal): RowState {
  if (g.moved) return "moved";
  return g.status;
}

/** Goals that hold one of the week's slots: everything but dropped and moved on. */
export function holdsSlot(g: Goal): boolean {
  return !g.moved && g.status !== "dropped";
}

/** Open first (in sort order), then done, then dropped, then moved on. */
export function ordered(goals: Goal[]): Goal[] {
  const rank: Record<RowState, number> = { open: 0, done: 1, dropped: 2, moved: 3 };
  return [...goals].sort(
    (a, b) =>
      rank[rowState(a)] - rank[rowState(b)] ||
      a.sort_order - b.sort_order ||
      a.created_at.localeCompare(b.created_at),
  );
}

export type Segment = "done" | "open" | "carried" | "dropped" | "empty";

/**
 * The 10-segment week strip. Live week: one segment per goal holding a slot
 * (done, then open), empty slots up to the cap. Past week (history): done,
 * carried (moved on), dropped. Past the cap the strip grows; those segments
 * are drawn in the warning colour.
 */
export function segments(goals: Goal[], mode: "live" | "history"): Segment[] {
  const list: Segment[] =
    mode === "live"
      ? [
          ...goals.filter((g) => holdsSlot(g) && g.status === "done").map(() => "done" as const),
          ...goals.filter((g) => holdsSlot(g) && g.status === "open").map(() => "open" as const),
        ]
      : [
          ...goals.filter((g) => g.status === "done" && !g.moved).map(() => "done" as const),
          ...goals.filter((g) => g.moved || g.status === "open").map(() => "carried" as const),
          ...goals.filter((g) => g.status === "dropped" && !g.moved).map(() => "dropped" as const),
        ];
  while (list.length < WEEKLY_CAP) list.push("empty");
  return list;
}

/** Dropped goals and goals moved to a later period: kept out of the way. */
export function isFolded(g: Goal): boolean {
  return g.moved || g.status === "dropped";
}

/** '2 done · 6 of 10': done, then every goal holding a slot, of the cap. */
export function countLabel(done: number, held: number): string {
  return `${done} done · ${held} of ${WEEKLY_CAP}`;
}

export function overCapMessage(held: number): string | null {
  return held > WEEKLY_CAP ? `${held} of ${WEEKLY_CAP}. Something's not getting done.` : null;
}
