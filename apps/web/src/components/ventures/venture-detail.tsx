"use client";

import { Check } from "@phosphor-icons/react";
import Link from "next/link";
import { useId, useOptimistic, useState, useTransition } from "react";

import {
  addDate,
  addLogEntry,
  addWorkstream,
  setDateDone,
  setVentureArchived,
  updateVenture,
  updateWorkstream,
  type VentureFields,
  type WorkstreamFields,
} from "@/app/(app)/ventures/actions";
import { Fold } from "@/components/ui/fold";
import type { ActionResult } from "@/lib/action-result";
import type { Goal } from "@/lib/goals/types";
import {
  LOG_KINDS,
  STAGES,
  STAGE_LABEL,
  WORKSTREAM_STATES,
  defaultDue,
  dueLabel,
  logStamp,
  metaLine,
  type LogEntry,
  type LogKind,
  type Stage,
  type Venture,
  type VentureDate,
  type Workstream,
} from "@/lib/ventures/types";

import { Chips, InlineText, MoreMenu, SectionLabel } from "./parts";

// /ventures/[slug]: one venture, readable in ten seconds. Hero, then NEXT
// (the largest text below the hero), blockers if any, workstreams, dates,
// this week's linked goals, notes and the running log. Every write is a
// server action applied optimistically; a failed save reverts and says why.

type Model = { venture: Venture; workstreams: Workstream[]; dates: VentureDate[]; log: LogEntry[] };

type Change =
  | { t: "venture"; patch: Partial<Venture> }
  | { t: "ws-add"; ws: Workstream }
  | { t: "ws"; id: string; patch: Partial<Workstream> }
  | { t: "date-add"; date: VentureDate }
  | { t: "date"; id: string; done: boolean }
  | { t: "log"; entry: LogEntry };

function reduce(m: Model, c: Change): Model {
  switch (c.t) {
    case "venture":
      return { ...m, venture: { ...m.venture, ...c.patch } };
    case "ws-add":
      return { ...m, workstreams: [...m.workstreams, c.ws] };
    case "ws":
      return { ...m, workstreams: m.workstreams.map((w) => (w.id === c.id ? { ...w, ...c.patch } : w)) };
    case "date-add":
      return { ...m, dates: [...m.dates, c.date] };
    case "date":
      return {
        ...m,
        dates: m.dates.map((d) => (d.id === c.id ? { ...d, done_at: c.done ? new Date().toISOString() : null } : d)),
      };
    case "log":
      return { ...m, log: [c.entry, ...m.log] };
  }
}

export function VentureDetail({
  venture,
  workstreams,
  dates,
  log,
  goals,
  today,
  startEditing = false,
}: Model & { goals: Goal[]; today: string; startEditing?: boolean }) {
  const [model, change] = useOptimistic<Model, Change>({ venture, workstreams, dates, log }, reduce);
  const [saving, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(startEditing);

  function run(c: Change, action: () => Promise<ActionResult>) {
    startTransition(async () => {
      change(c);
      try {
        const r = await action();
        setError(r.ok ? null : r.error);
      } catch {
        // Only the network can throw here: an action reports its own
        // failures in its result (src/lib/action-result.ts).
        setError("That didn't save. Check the connection and try again.");
      }
    });
  }

  const v = model.venture;
  const editVenture = (patch: VentureFields) =>
    run({ t: "venture", patch: patch as Partial<Venture> }, () => updateVenture(v.id, patch));
  const editWorkstream = (id: string, patch: WorkstreamFields) =>
    run({ t: "ws", id, patch: patch as Partial<Workstream> }, () => updateWorkstream(id, patch));
  const toggleDate = (d: VentureDate) =>
    run({ t: "date", id: d.id, done: !d.done_at }, () => setDateDone(d.id, !d.done_at));
  const newDate = (workstreamId: string | null, label: string, dueOn: string) =>
    run(
      {
        t: "date-add",
        date: { id: crypto.randomUUID(), venture_id: v.id, workstream_id: workstreamId, label, due_on: dueOn, done_at: null },
      },
      () => addDate(v.id, workstreamId, label, dueOn),
    );

  const shown = model.workstreams.filter((w) => w.state !== "parked");
  const parked = model.workstreams.filter((w) => w.state === "parked");
  const openDates = model.dates.filter((d) => !d.done_at).sort((a, b) => a.due_on.localeCompare(b.due_on));
  const doneDates = model.dates.filter((d) => d.done_at);
  const wsName = (id: string | null) => model.workstreams.find((w) => w.id === id)?.name ?? null;

  return (
    <article aria-busy={saving} data-testid="venture-detail" className="lg:max-w-3xl">
      <Link href="/ventures" className="tap -ml-3 mb-2 inline-flex items-center rounded-pill px-3 text-sm text-text-muted hover:bg-surface-2 hover:text-text">
        Ventures
      </Link>

      {/* Hero */}
      <header className="mb-10 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="font-display text-4xl text-text text-balance lg:text-display">{v.name}</h1>
          <p className="mt-2 flex flex-wrap gap-x-3 font-mono text-sm text-text-muted">
            {metaLine(v) && <span data-testid="venture-meta">{metaLine(v)}</span>}
            {v.archived_at && <span>archived</span>}
            {saving && <span>saving</span>}
          </p>
          {v.tagline && <p className="mt-2 text-sm text-text-muted text-pretty">{v.tagline}</p>}
        </div>
        <MoreMenu
          label="Venture actions"
          items={[
            { label: "Edit details", onSelect: () => setEditing(true) },
            v.archived_at
              ? { label: "Restore", onSelect: () => run({ t: "venture", patch: { archived_at: null } }, () => setVentureArchived(v.id, false)) }
              : {
                  label: "Archive",
                  danger: true,
                  onSelect: () =>
                    run({ t: "venture", patch: { archived_at: new Date().toISOString() } }, () => setVentureArchived(v.id, true)),
                },
          ]}
        />
      </header>

      {error && (
        <p role="alert" className="-mt-6 mb-8 text-sm text-danger">
          {error}
        </p>
      )}

      {editing && <DetailsForm venture={v} onCancel={() => setEditing(false)} onSave={(patch) => { setEditing(false); editVenture(patch); }} />}

      {/* NEXT: the largest text below the hero */}
      <section aria-label="Next action" className="mb-10">
        <SectionLabel>Next</SectionLabel>
        <InlineText
          testId="venture-next"
          label="Next action"
          value={v.next_action}
          placeholder="What's the one next move?"
          maxLength={300}
          className="text-2xl font-medium text-text"
          onSave={(next) => editVenture({ next_action: next })}
        />
      </section>

      {v.blockers && (
        <section aria-label="Blockers" className="relative mb-10 pl-4">
          <span aria-hidden className="absolute inset-y-0 left-0 w-0.5 bg-danger" />
          <SectionLabel>Blocked</SectionLabel>
          <InlineText
            label="Blockers"
            value={v.blockers}
            placeholder="Nothing blocking"
            multiline
            maxLength={2000}
            className="text-base text-danger"
            onSave={(next) => editVenture({ blockers: next })}
          />
        </section>
      )}

      {/* Workstreams */}
      <section aria-label="Workstreams" className="mb-10">
        <SectionLabel aside={`${model.workstreams.filter((w) => w.state === "active").length} active`}>Workstreams</SectionLabel>
        <ul className="border-t border-border">
          {shown.map((w) => (
            <WorkstreamRow
              key={w.id}
              ws={w}
              dates={model.dates.filter((d) => d.workstream_id === w.id)}
              today={today}
              onEdit={(patch) => editWorkstream(w.id, patch)}
              onToggleDate={toggleDate}
              onAddDate={(label, due) => newDate(w.id, label, due)}
            />
          ))}
        </ul>
        {parked.length > 0 && (
          <Fold label="Parked" count={parked.length} testId="workstreams-parked">
            {parked.map((w) => (
              <WorkstreamRow
                key={w.id}
                ws={w}
                dates={model.dates.filter((d) => d.workstream_id === w.id)}
                today={today}
                onEdit={(patch) => editWorkstream(w.id, patch)}
                onToggleDate={toggleDate}
                onAddDate={(label, due) => newDate(w.id, label, due)}
              />
            ))}
          </Fold>
        )}
        <AddLine
          action="+ Workstream"
          placeholder="Workstream name"
          onAdd={(name) =>
            run(
              {
                t: "ws-add",
                ws: { id: crypto.randomUUID(), venture_id: v.id, name, state: "active", next_action: null, notes: null, sort_order: 1000 },
              },
              () => addWorkstream(v.id, name),
            )
          }
        />
      </section>

      {/* Dates */}
      <section aria-label="Dates" className="mb-10">
        <SectionLabel aside={`${openDates.length} open`}>Dates</SectionLabel>
        {openDates.length === 0 ? (
          <p className="border-t border-border py-3 text-sm text-text-muted">No open dates.</p>
        ) : (
          <ul className="border-t border-border">
            {openDates.map((d) => (
              <DateRow key={d.id} date={d} workstream={wsName(d.workstream_id)} today={today} onToggle={() => toggleDate(d)} />
            ))}
          </ul>
        )}
        {doneDates.length > 0 && (
          <Fold label="Done" count={doneDates.length} testId="dates-done">
            {doneDates.map((d) => (
              <DateRow key={d.id} date={d} workstream={wsName(d.workstream_id)} today={today} onToggle={() => toggleDate(d)} />
            ))}
          </Fold>
        )}
        <DateForm today={today} onAdd={(label, due) => newDate(null, label, due)} />
      </section>

      {goals.length > 0 && (
        <section aria-label="This week's goals" className="mb-10">
          <SectionLabel>This week</SectionLabel>
          <ul className="border-t border-border">
            {goals.map((g) => (
              <li key={g.id} className="flex items-center gap-3 border-b border-border py-2.5 text-sm">
                <span
                  aria-hidden
                  className={`flex size-4 shrink-0 items-center justify-center rounded-pill border-2 ${
                    g.status === "done" ? "border-accent bg-accent text-on-accent" : "border-text-muted"
                  }`}
                >
                  {g.status === "done" && <Check size={10} weight="bold" />}
                </span>
                <span className={g.status === "open" ? "text-text" : "text-text-muted line-through"}>{g.title}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Notes */}
      <section aria-label="Notes" className="mb-10">
        <SectionLabel>Notes</SectionLabel>
        <InlineText
          label="Notes"
          value={v.notes}
          placeholder="Add notes"
          multiline
          maxLength={20000}
          className="text-base text-text"
          onSave={(next) => editVenture({ notes: next })}
        />
      </section>

      {/* Log */}
      <section aria-label="Log">
        <SectionLabel aside={`${model.log.length} ${model.log.length === 1 ? "entry" : "entries"}`}>Log</SectionLabel>
        <ul className="border-t border-border">
          {model.log.map((l) => (
            <LogRow key={l.id} entry={l} />
          ))}
        </ul>
        {/* At the end, as the brief has it; the list itself reads newest first. */}
        <div className="mt-4">
          <LogComposer
            onAdd={(kind, entry) =>
              run(
                { t: "log", entry: { id: `pending-${crypto.randomUUID()}`, kind, entry, created_at: new Date().toISOString(), pending: true } },
                () => addLogEntry(v.id, kind, entry),
              )
            }
          />
        </div>
      </section>
    </article>
  );
}

// ------------------------------------------------------------------ parts

function DetailsForm({
  venture,
  onSave,
  onCancel,
}: {
  venture: Venture;
  onSave: (patch: VentureFields) => void;
  onCancel: () => void;
}) {
  const [f, setF] = useState({
    name: venture.name,
    tagline: venture.tagline ?? "",
    role: venture.role ?? "",
    next_action: venture.next_action ?? "",
    blockers: venture.blockers ?? "",
  });
  const [stage, setStage] = useState<Stage | null>(venture.stage);
  const field = (key: keyof typeof f, label: string, max: number, multiline = false) => (
    <label className="block">
      <span className="label-mono text-text-muted">{label}</span>
      {multiline ? (
        <textarea
          value={f[key]}
          maxLength={max}
          rows={3}
          onChange={(e) => setF({ ...f, [key]: e.target.value })}
          className="mt-1 w-full resize-y border-b border-border bg-transparent pb-1 text-base text-text outline-none focus:border-accent"
        />
      ) : (
        <input
          value={f[key]}
          maxLength={max}
          onChange={(e) => setF({ ...f, [key]: e.target.value })}
          className="tap mt-1 w-full border-b border-border bg-transparent text-base text-text outline-none focus:border-accent"
        />
      )}
    </label>
  );

  return (
    <form
      data-testid="venture-details-form"
      aria-label="Edit details"
      className="mb-10 flex flex-col gap-5 border-y border-border py-6"
      onSubmit={(e) => {
        e.preventDefault();
        if (!f.name.trim()) return;
        onSave({
          name: f.name,
          tagline: f.tagline || null,
          role: f.role || null,
          stage,
          next_action: f.next_action || null,
          blockers: f.blockers || null,
        });
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
      }}
    >
      {field("name", "Name", 120)}
      {field("tagline", "Tagline", 200)}
      {field("role", "Your role", 120)}
      <div>
        <span className="label-mono text-text-muted">Stage</span>
        <div className="mt-2">
          <Chips label="Stage" options={STAGES} value={stage} onChange={setStage} render={(s) => STAGE_LABEL[s]} allowNone />
        </div>
      </div>
      {field("next_action", "Next action", 300)}
      {field("blockers", "Blockers", 2000, true)}
      <div className="flex gap-2">
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

export function WorkstreamRow({
  ws,
  dates,
  today,
  onEdit,
  onToggleDate,
  onAddDate,
}: {
  ws: Workstream;
  dates: VentureDate[];
  today: string;
  onEdit: (patch: WorkstreamFields) => void;
  onToggleDate: (d: VentureDate) => void;
  onAddDate: (label: string, due: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const panel = useId();
  const openCount = dates.filter((d) => !d.done_at).length;

  return (
    <li data-testid="workstream-row" data-state={ws.state} className="border-b border-border">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panel}
        onClick={() => setOpen((o) => !o)}
        className="tap flex w-full items-start gap-4 py-3 text-left"
      >
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-x-3">
            <span className={`text-base font-medium ${ws.state === "done" ? "text-text-muted line-through" : "text-text"}`}>{ws.name}</span>
            {ws.state !== "active" && <span className="font-mono text-xs text-text-muted">{ws.state}</span>}
          </span>
          {/* Open, the editable field below says it; closed, this line does. */}
          {ws.next_action && !open && <span className="mt-1 block text-sm text-text-muted text-pretty">{ws.next_action}</span>}
        </span>
        {openCount > 0 && (
          <span className="shrink-0 pt-0.5 font-mono text-xs text-text-muted tabular-nums">
            {openCount} {openCount === 1 ? "date" : "dates"}
          </span>
        )}
      </button>
      {open && (
        <div id={panel} className="flex flex-col gap-4 pb-5 pl-4">
          <Chips label={`${ws.name} state`} options={WORKSTREAM_STATES} value={ws.state} onChange={(s) => s && onEdit({ state: s })} />
          <div>
            <span className="label-mono text-text-muted">Next action</span>
            <InlineText
              label={`${ws.name} next action`}
              value={ws.next_action}
              placeholder="Add a next action"
              maxLength={300}
              className="text-base text-text"
              onSave={(next) => onEdit({ next_action: next })}
            />
          </div>
          <div>
            <span className="label-mono text-text-muted">Notes</span>
            <InlineText
              label={`${ws.name} notes`}
              value={ws.notes}
              placeholder="Add notes"
              multiline
              maxLength={20000}
              className="text-sm text-text"
              onSave={(next) => onEdit({ notes: next })}
            />
          </div>
          {dates.length > 0 && (
            <ul className="border-t border-border">
              {[...dates]
                .sort((a, b) => Number(!!a.done_at) - Number(!!b.done_at) || a.due_on.localeCompare(b.due_on))
                .map((d) => (
                  <DateRow key={d.id} date={d} today={today} onToggle={() => onToggleDate(d)} />
                ))}
            </ul>
          )}
          <DateForm today={today} onAdd={onAddDate} />
        </div>
      )}
    </li>
  );
}

function DateRow({
  date,
  workstream,
  today,
  onToggle,
}: {
  date: VentureDate;
  workstream?: string | null;
  today: string;
  onToggle: () => void;
}) {
  const done = !!date.done_at;
  const late = !done && date.due_on < today;
  return (
    <li data-testid="date-row" data-done={done || undefined} className="flex items-center gap-2 border-b border-border py-1">
      <button
        type="button"
        role="checkbox"
        aria-checked={done}
        aria-label={done ? `Reopen ${date.label}` : `Mark ${date.label} done`}
        onClick={onToggle}
        className="tap inline-flex shrink-0 items-center justify-center rounded-pill transition-colors hover:bg-surface-2"
      >
        <span
          className={`flex size-5 items-center justify-center rounded-pill border-2 transition-colors ${
            done ? "border-accent bg-accent text-on-accent" : "border-text-muted"
          }`}
        >
          {done && <Check size={12} weight="bold" aria-hidden />}
        </span>
      </button>
      <span className="min-w-0 flex-1">
        <span className={`block text-sm ${done ? "text-text-muted line-through" : "text-text"}`}>{date.label}</span>
        <span className="flex flex-wrap gap-x-3 font-mono text-xs text-text-muted">
          <span className={late ? "text-danger" : ""}>{dueLabel(date.due_on, today)}</span>
          {workstream && <span>{workstream}</span>}
        </span>
      </span>
    </li>
  );
}

function DateForm({ today, onAdd }: { today: string; onAdd: (label: string, due: string) => void }) {
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [due, setDue] = useState(defaultDue(today));
  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className="tap -ml-3 mt-1 inline-flex items-center rounded-pill px-3 text-sm text-accent hover:bg-surface-2">
        + Date
      </button>
    );
  }
  return (
    <form
      data-testid="date-form"
      className="mt-2 flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (!label.trim() || !due) return;
        onAdd(label.trim(), due);
        setLabel("");
        setOpen(false);
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") setOpen(false);
      }}
    >
      <input
        autoFocus
        value={label}
        maxLength={200}
        onChange={(e) => setLabel(e.target.value)}
        placeholder="What's due"
        aria-label="Date label"
        className="tap min-w-0 flex-1 border-b border-border bg-transparent text-sm text-text outline-none focus:border-accent"
      />
      <input
        type="date"
        value={due}
        onChange={(e) => setDue(e.target.value)}
        aria-label="Due on"
        className="tap border-b border-border bg-transparent font-mono text-xs text-text outline-none focus:border-accent"
      />
      <button type="submit" className="tap rounded-pill px-3 text-sm font-medium text-accent hover:bg-surface-2">
        Add
      </button>
    </form>
  );
}

function AddLine({ action, placeholder, onAdd }: { action: string; placeholder: string; onAdd: (text: string) => void }) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className="tap -ml-3 mt-1 inline-flex items-center rounded-pill px-3 text-sm text-accent hover:bg-surface-2">
        {action}
      </button>
    );
  }
  return (
    <form
      className="mt-2 flex items-center gap-2 border-b border-accent"
      onSubmit={(e) => {
        e.preventDefault();
        if (!value.trim()) return;
        onAdd(value.trim());
        setValue("");
        setOpen(false);
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") setOpen(false);
      }}
    >
      <input
        autoFocus
        value={value}
        maxLength={120}
        onChange={(e) => setValue(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className="tap min-w-0 flex-1 bg-transparent text-base text-text outline-none placeholder:text-text-muted"
      />
      <button type="submit" className="tap shrink-0 rounded-pill px-3 text-sm font-medium text-accent hover:bg-surface-2">
        Add
      </button>
    </form>
  );
}

function LogComposer({ onAdd }: { onAdd: (kind: LogKind, entry: string) => void }) {
  const [kind, setKind] = useState<(typeof LOG_KINDS)[number]>("note");
  const [value, setValue] = useState("");
  return (
    <form
      data-testid="log-composer"
      className="flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (!value.trim()) return;
        onAdd(kind, value.trim());
        setValue("");
        setKind("note");
      }}
    >
      <Chips label="Entry kind" options={LOG_KINDS} value={kind} onChange={(k) => k && setKind(k)} />
      <div className="flex items-center gap-2 border-b border-border focus-within:border-accent">
        <input
          value={value}
          maxLength={4000}
          onChange={(e) => setValue(e.target.value)}
          placeholder={`Add a ${kind}`}
          aria-label="Log entry"
          className="tap min-w-0 flex-1 bg-transparent text-base text-text outline-none placeholder:text-text-muted"
        />
        {value.trim() && (
          <button type="submit" className="tap shrink-0 rounded-pill px-3 text-sm font-medium text-accent hover:bg-surface-2">
            Add
          </button>
        )}
      </div>
    </form>
  );
}

export function LogRow({ entry }: { entry: LogEntry }) {
  const auto = entry.kind === "auto";
  const marked = entry.kind === "decision" || entry.kind === "milestone";
  return (
    <li data-testid="log-entry" data-kind={entry.kind} className="border-b border-border py-3">
      <p className="flex flex-wrap gap-x-3 font-mono text-xs text-text-muted">
        <span>{logStamp(entry.created_at)}</span>
        {/* Outlined, not just accent-coloured: Day's accent is nearly the
            body text's brown, and a decision must stand out in both themes. */}
        {marked && <span className="rounded-pill border border-current px-1.5 text-accent">{entry.kind}</span>}
        {auto && <span>auto</span>}
        {entry.pending && <span>saving</span>}
      </p>
      <p className={`mt-1 text-pretty ${auto ? "text-sm text-text-muted" : "text-base text-text"}`}>{entry.entry}</p>
    </li>
  );
}
