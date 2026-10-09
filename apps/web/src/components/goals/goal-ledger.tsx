"use client";

import { startTransition, useOptimistic, useState } from "react";

import {
  completeGoal,
  createGoal,
  dropGoal,
  editGoal,
  moveGoalToNext,
  uncompleteGoal,
} from "@/app/(app)/goals/actions";
import { WEEKLY_CAP, type Area, type Horizon } from "@/lib/goals/period";
import { holdsSlot, ordered, overCapMessage, segments, type Goal, type GoalStatus } from "@/lib/goals/types";

import { AreaChips, GoalRow } from "./goal-row";
import { ProgressStrip } from "./progress-strip";

type Change =
  | { type: "add"; goal: Goal }
  | { type: "status"; id: string; status: GoalStatus }
  | { type: "edit"; id: string; title: string; area: Area | null }
  | { type: "move"; id: string };

function reduce(goals: Goal[], c: Change): Goal[] {
  switch (c.type) {
    case "add":
      return [...goals, c.goal];
    case "status":
      return goals.map((g) =>
        g.id === c.id
          ? { ...g, status: c.status, completed_at: c.status === "done" ? new Date().toISOString() : null }
          : g,
      );
    case "edit":
      return goals.map((g) => (g.id === c.id ? { ...g, title: c.title, area: c.area } : g));
    case "move":
      return goals.map((g) => (g.id === c.id ? { ...g, moved: true } : g));
  }
}

const COPY: Record<Horizon, { heading: string; empty: string; move?: string; placeholder: string }> = {
  weekly: {
    heading: "This week",
    empty: "Nothing set for this week yet.",
    move: "Next week",
    placeholder: "Add a goal for this week",
  },
  monthly: {
    heading: "This month",
    empty: "Nothing set for this month yet.",
    move: "Next month",
    placeholder: "Add a goal for this month",
  },
  long_term: {
    heading: "Long term",
    empty: "Nothing you're building toward, written down yet.",
    placeholder: "Add a long-term goal",
  },
};

/**
 * One period's goals as a ledger. Taps apply at once (useOptimistic) and the
 * server render that follows each action replaces the optimistic list.
 */
export function GoalLedger({
  goals,
  horizon,
  periodLabel,
  composer = "inline",
}: {
  goals: Goal[];
  horizon: Horizon;
  /** Shown beside the heading: 'Oct 5 – 11', 'October 2026'. */
  periodLabel?: string;
  /** "pinned": fixed above the dock on phones (the Goals page). */
  composer?: "inline" | "pinned";
}) {
  const [list, change] = useOptimistic(goals, reduce);
  const [error, setError] = useState<string | null>(null);
  const copy = COPY[horizon];

  function run(c: Change, action: () => Promise<void>) {
    startTransition(async () => {
      change(c);
      try {
        await action();
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : "That didn't save. Try again.");
      }
    });
  }

  const rows = ordered(list);
  const weekly = horizon === "weekly";
  const held = list.filter(holdsSlot);
  const done = held.filter((g) => g.status === "done").length;
  // Over-cap rows: the slot holders past the tenth, in display order.
  const overCapIds = new Set(weekly ? rows.filter(holdsSlot).slice(WEEKLY_CAP).map((g) => g.id) : []);
  const warning = weekly ? overCapMessage(held.length) : null;

  return (
    <section aria-label={copy.heading} data-testid={`goals-${horizon}`}>
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="font-display text-3xl text-text">
          {copy.heading}
          {periodLabel && <span className="sr-only">, {periodLabel}</span>}
        </h2>
        <p className="font-mono text-sm text-text-muted tabular-nums">
          {weekly ? (
            <span data-testid="goals-fraction">
              {done}/{Math.max(WEEKLY_CAP, held.length)}
            </span>
          ) : (
            periodLabel
          )}
        </p>
      </div>
      {weekly && periodLabel && <p className="mt-1 text-sm text-text-muted">{periodLabel}</p>}

      {weekly && (
        <div className="mt-4">
          <ProgressStrip segments={segments(list, "live")} />
          {warning && (
            <p role="status" data-testid="cap-warning" className="mt-3 text-sm text-danger">
              {warning}
            </p>
          )}
        </div>
      )}

      {rows.length === 0 ? (
        <p className="mt-8 font-display text-2xl text-text-muted text-balance">{copy.empty}</p>
      ) : (
        <ul className="mt-5 border-t border-border">
          {rows.map((g) => (
            <GoalRow
              key={g.id}
              goal={g}
              overCap={overCapIds.has(g.id)}
              moveLabel={copy.move}
              actions={
                g.pending
                  ? undefined
                  : {
                      onToggle: () =>
                        g.status === "done"
                          ? run({ type: "status", id: g.id, status: "open" }, () => uncompleteGoal(g.id))
                          : run({ type: "status", id: g.id, status: "done" }, () => completeGoal(g.id)),
                      onDrop: () => run({ type: "status", id: g.id, status: "dropped" }, () => dropGoal(g.id)),
                      onRestore: () => run({ type: "status", id: g.id, status: "open" }, () => uncompleteGoal(g.id)),
                      onMove: copy.move ? () => run({ type: "move", id: g.id }, () => moveGoalToNext(g.id)) : undefined,
                      onEdit: (title, area) =>
                        run({ type: "edit", id: g.id, title, area }, () => editGoal(g.id, { title, area })),
                    }
              }
            />
          ))}
        </ul>
      )}

      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}

      <Composer
        horizon={horizon}
        placeholder={copy.placeholder}
        pinned={composer === "pinned"}
        onAdd={(title, area) => {
          const goal: Goal = {
            id: crypto.randomUUID(),
            title,
            notes: null,
            horizon,
            area,
            period_start: null,
            status: "open",
            carried_from: null,
            carry_count: 0,
            sort_order: 0,
            created_at: new Date().toISOString(),
            completed_at: null,
            moved: false,
            pending: true,
          };
          run({ type: "add", goal }, () => createGoal({ title, horizon, area }));
        }}
      />
    </section>
  );
}

function Composer({
  horizon,
  placeholder,
  pinned,
  onAdd,
}: {
  horizon: Horizon;
  placeholder: string;
  pinned: boolean;
  onAdd: (title: string, area: Area | null) => void;
}) {
  const [value, setValue] = useState("");
  const [area, setArea] = useState<Area | null>(null);
  const typing = value.trim().length > 0;

  return (
    <form
      data-testid="goal-composer"
      onSubmit={(e) => {
        e.preventDefault();
        const title = value.trim().replace(/\s+/g, " ");
        if (!title) return;
        setValue("");
        setArea(null);
        onAdd(title, area);
      }}
      className={
        pinned
          ? "fixed inset-x-0 bottom-dock z-30 border-t border-border bg-bg px-safe py-3 lg:static lg:mt-6 lg:border-0 lg:bg-transparent lg:p-0"
          : "mt-6"
      }
    >
      {typing && <AreaChips value={area} onChange={setArea} />}
      <div className={`flex items-center gap-2 border-b border-border focus-within:border-accent ${typing ? "mt-2" : ""}`}>
        <input
          name={`new-${horizon}-goal`}
          value={value}
          maxLength={200}
          autoComplete="off"
          enterKeyHint="done"
          onChange={(e) => setValue(e.target.value)}
          placeholder={placeholder}
          aria-label={placeholder}
          className="tap min-w-0 flex-1 bg-transparent text-base text-text outline-none placeholder:text-text-muted"
        />
        {typing && (
          <button type="submit" className="tap shrink-0 rounded-pill px-3 text-sm font-medium text-accent hover:bg-surface-2">
            Add
          </button>
        )}
      </div>
    </form>
  );
}
