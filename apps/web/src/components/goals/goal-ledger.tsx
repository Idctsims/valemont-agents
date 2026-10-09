"use client";

import { useOptimistic, useState, useTransition } from "react";

import {
  completeGoal,
  createGoal,
  dropGoal,
  editGoal,
  moveGoalToNext,
  uncompleteGoal,
} from "@/app/(app)/goals/actions";
import { WEEKLY_CAP, type Area, type Horizon } from "@/lib/goals/period";
import {
  countLabel,
  holdsSlot,
  isFolded,
  ordered,
  overCapMessage,
  segments,
  type Goal,
  type GoalStatus,
  weekTally,
} from "@/lib/goals/types";

import { Fold } from "@/components/ui/fold";

import { AreaChips, GoalRow, VentureChips, type VentureOption } from "./goal-row";
import { ProgressStrip } from "./progress-strip";

type Change =
  | { type: "add"; goal: Goal }
  | { type: "status"; id: string; status: GoalStatus }
  | { type: "edit"; id: string; title: string; area: Area | null; ventureId: string | null }
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
      return goals.map((g) => (g.id === c.id ? { ...g, title: c.title, area: c.area, venture_id: c.ventureId } : g));
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
    empty: "Nothing long-term written down yet.",
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
  header = "label",
  ventures = [],
  ventureNames = {},
}: {
  goals: Goal[];
  horizon: Horizon;
  /** Active ventures, offered as chips when a goal's area is business. */
  ventures?: VentureOption[];
  /** Every venture's name by id, for the tag line of a linked goal. */
  ventureNames?: Record<string, string>;
  /** 'Oct 5 – 11', 'October 2026'. */
  periodLabel?: string;
  /** "label" where the page has another hero (Home's date); "hero" on /goals. */
  header?: "label" | "hero";
  /** "pinned": fixed above the dock on phones (the Goals page). */
  composer?: "inline" | "pinned";
}) {
  const [list, change] = useOptimistic(goals, reduce);
  // Actions run one at a time, and until the last one lands the screen is
  // ahead of the database. Say so, quietly, so a tap isn't lost to an app
  // closed too soon.
  const [saving, startTransition] = useTransition();
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
  const active = rows.filter((g) => !isFolded(g));
  const folded = rows.filter(isFolded);
  const weekly = horizon === "weekly";
  const { done, held } = weekTally(list);
  // Over-cap rows: the slot holders past the tenth, in display order.
  const overCapIds = new Set(weekly ? active.filter(holdsSlot).slice(WEEKLY_CAP).map((g) => g.id) : []);
  const warning = weekly ? overCapMessage(held) : null;

  const rowFor = (g: Goal) => (
    <GoalRow
      key={g.id}
      goal={g}
      overCap={overCapIds.has(g.id)}
      moveLabel={copy.move}
      ventureName={g.venture_id ? ventureNames[g.venture_id] : undefined}
      ventures={ventures}
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
              onEdit: (title, area, ventureId) =>
                run({ type: "edit", id: g.id, title, area, ventureId }, () =>
                  editGoal(g.id, { title, area, ventureId }),
                ),
            }
      }
    />
  );

  return (
    <section aria-label={copy.heading} aria-busy={saving} data-testid={`goals-${horizon}`}>
      <LedgerHeader
        variant={header}
        heading={copy.heading}
        periodLabel={periodLabel}
        count={weekly ? countLabel(done, held) : undefined}
        saving={saving}
      />

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

      {active.length === 0 ? (
        <p className="mt-8 font-display text-2xl text-text-muted text-balance">{copy.empty}</p>
      ) : (
        <ul className="mt-5 border-t border-border">{active.map(rowFor)}</ul>
      )}
      {folded.length > 0 && <MovedOnFold count={folded.length}>{folded.map(rowFor)}</MovedOnFold>}

      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}

      <Composer
        horizon={horizon}
        placeholder={copy.placeholder}
        pinned={composer === "pinned"}
        ventures={ventures}
        onAdd={(title, area, ventureId) => {
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
            venture_id: ventureId,
            sort_order: 0,
            created_at: new Date().toISOString(),
            completed_at: null,
            moved: false,
            pending: true,
          };
          run({ type: "add", goal }, () => createGoal({ title, horizon, area, ventureId }));
        }}
      />
    </section>
  );
}

/**
 * One hero per screen. "label": a small mono section label with the period
 * beside it, for a page whose hero is something else (Home's date). "hero":
 * the period itself in the display serif, for /goals, whose tabs already
 * name the view. The count reads done, then held of the cap, matching the
 * strip: filled = done, outlined = open, faint = free.
 */
export function LedgerHeader({
  variant,
  heading,
  periodLabel,
  count,
  saving = false,
}: {
  variant: "label" | "hero";
  heading: string;
  periodLabel?: string;
  count?: string;
  saving?: boolean;
}) {
  const status = (count || saving) && (
    <p data-testid="goals-fraction" className="shrink-0 font-mono text-xs text-text-muted tabular-nums">
      {saving && <span className="mr-3">saving</span>}
      {count}
    </p>
  );

  if (variant === "label") {
    return (
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="flex items-baseline gap-3">
          <span className="label-mono text-text-muted">{heading}</span>
          {periodLabel && <span className="font-mono text-xs text-text">{periodLabel}</span>}
        </h2>
        {status}
      </div>
    );
  }
  return (
    <div>
      <h2 className="font-display text-4xl text-text lg:text-display">
        {periodLabel ?? heading}
        {periodLabel && <span className="sr-only"> ({heading})</span>}
      </h2>
      {status && <div className="mt-2">{status}</div>}
    </div>
  );
}

/**
 * Dropped goals and goals moved on to the next period, folded away under one
 * line. Collapsed by default; a tap opens it.
 */
export function MovedOnFold({
  count,
  defaultOpen = false,
  children,
}: {
  count: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  return (
    <Fold label="Moved on" count={count} defaultOpen={defaultOpen} testId="moved-on">
      {children}
    </Fold>
  );
}

function Composer({
  horizon,
  placeholder,
  pinned,
  ventures,
  onAdd,
}: {
  horizon: Horizon;
  placeholder: string;
  pinned: boolean;
  ventures: VentureOption[];
  onAdd: (title: string, area: Area | null, ventureId: string | null) => void;
}) {
  const [value, setValue] = useState("");
  const [area, setArea] = useState<Area | null>(null);
  const [venture, setVenture] = useState<string | null>(null);
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
        setVenture(null);
        onAdd(title, area, area === "business" ? venture : null);
      }}
      className={
        pinned
          ? "fixed inset-x-0 bottom-dock z-30 border-t border-border bg-bg px-safe py-3 lg:static lg:mt-6 lg:border-0 lg:bg-transparent lg:p-0"
          : "mt-6"
      }
    >
      {typing && <AreaChips value={area} onChange={setArea} />}
      {typing && area === "business" && <VentureChips ventures={ventures} value={venture} onChange={setVenture} />}
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
