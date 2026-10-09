"use client";

import { ArrowBendUpRight, Check, DotsThree } from "@phosphor-icons/react";
import { useEffect, useId, useRef, useState } from "react";

import { AREAS, type Area } from "@/lib/goals/period";
import { rowState, type Goal } from "@/lib/goals/types";

// One goal, as a ledger line: a completion ring, the title, a mono area tag
// and, for a goal that has slipped, "↻ n". Full-bleed, separated by hairline
// rules; no card, no shadow.
//
// Gestures, each with a tap path in the row menu:
//   swipe right  complete (or reopen a done goal)
//   swipe left   reveal Next week / Drop
// Without handlers (the /design showcase, history) the row is static.

export type GoalRowActions = {
  onToggle: () => void;
  onDrop: () => void;
  onRestore: () => void;
  onMove?: () => void;
  onEdit: (title: string, area: Area | null) => void;
};

/** Must match --vm-swipe-commit (4.5rem) in tokens.css. */
const COMMIT_PX = 72;
/** Movement before a drag picks an axis; below this it is still a tap. */
const AXIS_PX = 8;

export function GoalRow({
  goal,
  actions,
  moveLabel,
  overCap = false,
}: {
  goal: Goal;
  actions?: GoalRowActions;
  /** "Next week" or "Next month"; absent where a goal cannot move on. */
  moveLabel?: string;
  /** This row is past the week's soft cap of ten. */
  overCap?: boolean;
}) {
  const state = rowState(goal);
  const live = !!actions && state !== "moved";
  const [editing, setEditing] = useState(false);

  // ---- swipe
  const [dx, setDx] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [revealed, setRevealed] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; base: number; axis: "x" | "y" | null } | null>(null);
  const panelWidth = () => panel.current?.offsetWidth ?? 0;
  const canReveal = live && state === "open";

  function onPointerDown(e: React.PointerEvent) {
    if (!live || editing || e.button !== 0) return;
    if ((e.target as Element).closest("button, input, a")) return;
    drag.current = { x: e.clientX, y: e.clientY, base: revealed ? -panelWidth() : 0, axis: null };
  }

  function onPointerMove(e: React.PointerEvent) {
    const d = drag.current;
    if (!d) return;
    const mx = e.clientX - d.x;
    const my = e.clientY - d.y;
    if (!d.axis) {
      if (Math.abs(mx) < AXIS_PX && Math.abs(my) < AXIS_PX) return;
      d.axis = Math.abs(mx) > Math.abs(my) ? "x" : "y";
      if (d.axis === "x") {
        (e.currentTarget as Element).setPointerCapture(e.pointerId);
        setDragging(true);
      }
    }
    if (d.axis !== "x") return;
    const min = canReveal ? -panelWidth() : 0;
    setDx(Math.max(min, Math.min(COMMIT_PX * 1.4, d.base + mx)));
  }

  function onPointerEnd() {
    const d = drag.current;
    drag.current = null;
    if (!d || d.axis !== "x") return;
    setDragging(false);
    if (dx >= COMMIT_PX && !revealed && state !== "dropped") {
      setDx(0);
      actions?.onToggle();
    } else if (canReveal && dx <= -COMMIT_PX / 2) {
      setDx(-panelWidth());
      setRevealed(true);
    } else {
      setDx(0);
      setRevealed(false);
    }
  }

  function closeReveal() {
    setDx(0);
    setRevealed(false);
  }

  const done = state === "done";
  const struck = done || state === "dropped";

  return (
    <li
      data-testid="goal-row"
      data-state={state}
      data-over-cap={overCap || undefined}
      className="relative overflow-hidden border-b border-border"
    >
      {/* Behind the row: the complete hint (right swipe) or the actions (left). */}
      {live && (
        <div aria-hidden={!revealed} className="absolute inset-0 flex items-stretch justify-between">
          <span
            className={`flex items-center pl-4 text-accent transition-opacity ${dx > 0 ? "opacity-100" : "opacity-0"}`}
          >
            <Check size={22} weight="bold" aria-hidden />
          </span>
          {canReveal && (
            <div ref={panel} className="flex">
              {actions?.onMove && moveLabel && (
                <button
                  type="button"
                  tabIndex={revealed ? 0 : -1}
                  onClick={() => {
                    closeReveal();
                    actions.onMove?.();
                  }}
                  className="w-swipe bg-surface-2 px-2 text-sm text-text"
                >
                  {moveLabel}
                </button>
              )}
              <button
                type="button"
                tabIndex={revealed ? 0 : -1}
                onClick={() => {
                  closeReveal();
                  actions?.onDrop();
                }}
                className="w-swipe bg-danger-fill px-2 text-sm text-danger"
              >
                Drop
              </button>
            </div>
          )}
        </div>
      )}

      <div
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerEnd}
        onPointerCancel={onPointerEnd}
        style={dx ? { transform: `translateX(${dx}px)` } : undefined}
        className={`relative flex touch-pan-y items-start gap-2 bg-bg py-2 ${
          dragging ? "" : "transition-transform"
        }`}
      >
        {overCap && <span aria-hidden className="absolute inset-y-3 left-0 w-0.5 bg-danger" />}

        <Ring goal={goal} onToggle={live ? actions?.onToggle : undefined} />

        <div className={`min-w-0 flex-1 py-1.5 transition-opacity ${struck || state === "moved" ? "opacity-55" : ""}`}>
          {editing && actions ? (
            <EditForm
              goal={goal}
              onCancel={() => setEditing(false)}
              onSave={(title, area) => {
                setEditing(false);
                actions.onEdit(title, area);
              }}
            />
          ) : (
            <>
              <p className="text-base text-text text-pretty">
                <span className={`strike-draw ${struck ? "strike-draw-on" : ""}`}>{goal.title}</span>
              </p>
              <Meta goal={goal} overCap={overCap} />
            </>
          )}
        </div>

        {live && !editing && (
          <RowMenu
            goal={goal}
            moveLabel={moveLabel}
            actions={actions!}
            onEdit={() => {
              closeReveal();
              setEditing(true);
            }}
          />
        )}
      </div>
    </li>
  );
}

function Ring({ goal, onToggle }: { goal: Goal; onToggle?: () => void }) {
  const state = rowState(goal);
  const mark =
    state === "moved" ? (
      <ArrowBendUpRight size={18} className="text-text-muted" aria-hidden />
    ) : (
      <span
        className={`flex size-6 items-center justify-center rounded-pill border-2 transition-colors ${
          state === "done"
            ? "border-accent bg-accent text-on-accent"
            : state === "dropped"
              ? "border-dashed border-dead"
              : "border-text-muted"
        }`}
      >
        {state === "done" && <Check size={14} weight="bold" aria-hidden />}
      </span>
    );

  if (!onToggle) {
    return <span className="tap inline-flex shrink-0 items-center justify-center">{mark}</span>;
  }
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={state === "done"}
      aria-label={state === "done" ? `Reopen ${goal.title}` : `Complete ${goal.title}`}
      onClick={onToggle}
      disabled={state === "dropped"}
      className="tap inline-flex shrink-0 items-center justify-center rounded-pill transition-colors hover:bg-surface-2 disabled:cursor-default disabled:hover:bg-transparent"
    >
      {mark}
    </button>
  );
}

function Meta({ goal, overCap }: { goal: Goal; overCap: boolean }) {
  const state = rowState(goal);
  const bits: React.ReactNode[] = [];
  if (goal.area) bits.push(<span key="area">{goal.area}</span>);
  if (goal.carry_count > 0) {
    bits.push(
      <span key="carry" title={`Carried over ${goal.carry_count} time${goal.carry_count === 1 ? "" : "s"}`}>
        <span aria-hidden>↻ {goal.carry_count}</span>
        <span className="sr-only">carried over {goal.carry_count} times</span>
      </span>,
    );
  }
  if (state === "moved") bits.push(<span key="moved">moved on</span>);
  if (state === "dropped") bits.push(<span key="dropped">dropped</span>);
  if (overCap) bits.push(<span key="over" className="text-danger">over ten</span>);
  if (goal.pending) bits.push(<span key="pending">saving</span>);
  if (bits.length === 0) return null;
  return <p className="mt-1 flex flex-wrap gap-x-3 font-mono text-xs text-text-muted">{bits}</p>;
}

function EditForm({
  goal,
  onSave,
  onCancel,
}: {
  goal: Goal;
  onSave: (title: string, area: Area | null) => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState(goal.title);
  const [area, setArea] = useState<Area | null>(goal.area);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (title.trim()) onSave(title, area);
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
      }}
    >
      <input
        ref={input}
        value={title}
        maxLength={200}
        onChange={(e) => setTitle(e.target.value)}
        aria-label="Goal title"
        className="w-full border-b border-accent bg-transparent pb-1 text-base text-text outline-none"
      />
      <AreaChips value={area} onChange={setArea} />
      <div className="mt-2 flex gap-2">
        <button type="submit" className="tap rounded-pill px-3 text-sm font-medium text-accent hover:bg-surface-2">
          Save
        </button>
        <button type="button" onClick={onCancel} className="tap rounded-pill px-3 text-sm text-text-muted hover:bg-surface-2">
          Cancel
        </button>
      </div>
    </form>
  );
}

export function AreaChips({ value, onChange }: { value: Area | null; onChange: (a: Area | null) => void }) {
  return (
    <div role="group" aria-label="Area" className="mt-2 flex flex-wrap gap-1.5">
      {AREAS.map((a) => (
        <button
          key={a}
          type="button"
          aria-pressed={value === a}
          onClick={() => onChange(value === a ? null : a)}
          className={`inline-flex h-8 items-center rounded-pill border px-3 font-mono text-xs transition-colors ${
            value === a
              ? "border-accent bg-accent text-on-accent"
              : "border-border text-text-muted hover:bg-surface-2 hover:text-text"
          }`}
        >
          {a}
        </button>
      ))}
    </div>
  );
}

function RowMenu({
  goal,
  moveLabel,
  actions,
  onEdit,
}: {
  goal: Goal;
  moveLabel?: string;
  actions: GoalRowActions;
  onEdit: () => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const item = "tap flex w-full items-center rounded-inner px-3 text-left text-sm transition-colors hover:bg-surface-2";
  const pick = (fn: () => void) => () => {
    setOpen(false);
    fn();
  };

  return (
    <div ref={root} className="relative shrink-0">
      <button
        type="button"
        aria-label={`Actions for ${goal.title}`}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((o) => !o)}
        className={`tap inline-flex items-center justify-center rounded-pill transition-colors hover:bg-surface-2 hover:text-text ${
          open ? "bg-surface-2 text-text" : "text-text-muted"
        }`}
      >
        <DotsThree size={22} weight="bold" aria-hidden />
      </button>
      {open && (
        <div
          id={id}
          role="menu"
          className="absolute top-full right-0 z-20 mt-1 min-w-44 rounded-inner border border-border bg-surface p-1"
        >
          <button type="button" role="menuitem" className={`${item} text-text`} onClick={pick(onEdit)}>
            Edit
          </button>
          {goal.status !== "dropped" && (
            <button type="button" role="menuitem" className={`${item} text-text`} onClick={pick(actions.onToggle)}>
              {goal.status === "done" ? "Reopen" : "Mark done"}
            </button>
          )}
          {goal.status === "open" && actions.onMove && moveLabel && (
            <button type="button" role="menuitem" className={`${item} text-text`} onClick={pick(actions.onMove)}>
              Move to {moveLabel.toLowerCase()}
            </button>
          )}
          {goal.status === "open" && (
            <button type="button" role="menuitem" className={`${item} text-danger`} onClick={pick(actions.onDrop)}>
              Drop
            </button>
          )}
          {goal.status === "dropped" && (
            <button type="button" role="menuitem" className={`${item} text-text`} onClick={pick(actions.onRestore)}>
              Restore
            </button>
          )}
        </div>
      )}
    </div>
  );
}
