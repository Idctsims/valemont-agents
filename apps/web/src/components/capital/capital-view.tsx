"use client";

import dynamic from "next/dynamic";
import { useEffect, useId, useOptimistic, useRef, useState, useTransition } from "react";

import { addEntry } from "@/app/(app)/capital/actions";
import { Chips, MoreMenu, SectionLabel } from "@/components/ventures/parts";
import type { CapitalData } from "@/lib/capital/data";
import {
  KINDS,
  RANGES,
  applyEntry,
  changeLabel,
  changeOf,
  correctionFor,
  emptyMode,
  entryStamp,
  inRange,
  money,
  parseAmount,
  series,
  share,
  signedCents,
  signedMoney,
  sourceLabel,
  type Entry,
  type Kind,
  type Mode,
  type ModeToday,
  type Range,
  type SourceToday,
} from "@/lib/capital/types";

// /capital. Paper first, always labelled PAPER; a LIVE section appears only
// once live rows exist, with its own total, change and chart, and is never
// summed with paper. Entries are append-only: no edit, no delete; the ⋯ menu
// offers "Correct with adjustment", which pre-fills the composer with the
// negating amount. Writes are server actions applied optimistically; a
// failed save reverts and says why.

// Recharts stays out of every other route's bundle.
const CapitalChart = dynamic(() => import("./capital-chart"), {
  ssr: false,
  loading: () => <div className="h-48 w-full lg:h-64" />,
});

type Model = { modes: ModeToday[]; entries: Entry[] };

function reduce(m: Model, entry: Entry): Model {
  const has = m.modes.some((x) => x.mode === entry.mode);
  const modes = (has ? m.modes : [...m.modes, emptyMode(entry.mode)]).map((x) => applyEntry(x, entry));
  return { modes, entries: [entry, ...m.entries] };
}

export type Draft = {
  kind: Kind;
  /** For an adjustment only: which way it moves the balance. */
  sign: "add" | "subtract";
  amount: string;
  note: string;
  mode: Mode;
  /** What was typed into the LIVE confirmation. */
  confirm: string;
};

export const BLANK: Draft = { kind: "deposit", sign: "add", amount: "", note: "", mode: "paper", confirm: "" };

export function CapitalView({ data, today }: { data: CapitalData; today: string }) {
  const [model, add] = useOptimistic<Model, Entry>({ modes: data.modes, entries: data.entries }, reduce);
  const [saving, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(BLANK);
  const composer = useRef<HTMLFormElement>(null);

  const paper = model.modes.find((m) => m.mode === "paper") ?? emptyMode("paper");
  const live = model.modes.find((m) => m.mode === "live");
  const showModeTags = !!live;

  function submit(entry: Omit<Entry, "id" | "created_at" | "pending">, confirm: string) {
    startTransition(async () => {
      add({ ...entry, id: `pending-${crypto.randomUUID()}`, created_at: new Date().toISOString(), pending: true });
      try {
        await addEntry({
          mode: entry.mode,
          kind: entry.kind,
          cents: entry.amount,
          note: entry.note,
          confirm,
        });
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : "That didn't save. Try again.");
      }
    });
  }

  function correct(e: Entry) {
    const cents = correctionFor(e);
    setDraft({
      kind: "adjustment",
      sign: cents < 0 ? "subtract" : "add",
      amount: (Math.abs(cents) / 100).toFixed(2),
      note: `Correction: ${e.kind} ${signedMoney(signedCents(e))}, ${entryStamp(e.created_at)}`,
      mode: e.mode,
      confirm: "",
    });
    composer.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    composer.current?.querySelector<HTMLInputElement>('input[name="amount"]')?.focus({ preventScroll: true });
  }

  return (
    <div aria-busy={saving} data-testid="capital" className="lg:max-w-3xl">
      <h1 className="sr-only">Capital</h1>

      <ModeSection mode={paper} snapshots={data.snapshots.paper} today={today} saving={saving} hero />

      {error && (
        <p role="alert" className="mt-4 text-sm text-danger">
          {error}
        </p>
      )}

      {live && (
        <div className="mt-14 border-t rule-wood pt-8">
          <ModeSection mode={live} snapshots={data.snapshots.live} today={today} saving={saving} />
        </div>
      )}

      <section aria-label="Entries" className="mt-12">
        <SectionLabel aside={`${model.entries.length} ${model.entries.length === 1 ? "entry" : "entries"}`}>
          Entries
        </SectionLabel>
        {model.entries.length === 0 ? (
          <p className="border-t border-border py-3 text-sm text-text-muted">No entries yet.</p>
        ) : (
          <ul data-testid="entries" className="border-t border-border">
            {model.entries.map((e) => (
              <EntryRow key={e.id} entry={e} showMode={showModeTags} onCorrect={() => correct(e)} />
            ))}
          </ul>
        )}
        <Composer
          ref={composer}
          draft={draft}
          onChange={setDraft}
          onSubmit={(entry, confirm) => {
            submit(entry, confirm);
            setDraft(BLANK);
          }}
        />
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- a mode

export function ModeSection({
  mode,
  snapshots,
  today,
  saving,
  hero = false,
}: {
  mode: ModeToday;
  snapshots: { snap_date: string; value: number }[];
  today: string;
  saving: boolean;
  /** The page's one hero (paper). Live gets a smaller head of its own. */
  hero?: boolean;
}) {
  const change = changeOf(mode.total);
  const label = mode.mode === "live" ? "Live capital" : "Paper capital";
  return (
    <section aria-label={label} data-testid={`capital-${mode.mode}`}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <p
          data-testid="capital-total"
          className={`font-display text-text tabular-nums ${hero ? "text-display" : "text-4xl"}`}
        >
          {money(mode.total.value)}
        </p>
        <ModeTag mode={mode.mode} />
      </div>
      <p className="mt-2 flex flex-wrap gap-x-3 font-mono text-sm tabular-nums">
        <span data-testid="capital-change" className={toneClass(change?.tone)}>
          {changeLabel(change)}
        </span>
        {saving && <span className="text-text-muted">saving</span>}
      </p>

      <History mode={mode} snapshots={snapshots} today={today} />
      <Breakdown mode={mode} />
    </section>
  );
}

/** PAPER or LIVE, beside the total. A status chip, never small grey text. */
export function ModeTag({ mode }: { mode: Mode }) {
  return (
    <span
      data-testid="mode-tag"
      className={`label-mono inline-flex h-8 shrink-0 items-center gap-1.5 rounded-pill border px-3 ${
        mode === "live" ? "border-live bg-live-fill text-live" : "border-on-pace bg-on-pace-fill text-on-pace"
      }`}
    >
      <span aria-hidden className="size-1.5 rounded-pill bg-current" />
      {mode === "live" ? "Live" : "Paper"}
    </span>
  );
}

function toneClass(tone: "up" | "down" | "flat" | undefined): string {
  return tone === "up" ? "text-hit" : tone === "down" ? "text-danger" : "text-text-muted";
}

function History({
  mode,
  snapshots,
  today,
}: {
  mode: ModeToday;
  snapshots: { snap_date: string; value: number }[];
  today: string;
}) {
  const [range, setRange] = useState<Range>("1M");
  const all = series(snapshots, today, mode.total.value);
  const points = inRange(all, range, today);

  return (
    <div className="mt-8" data-testid="capital-history">
      {all.length < 2 ? (
        <p className="border-y border-border py-4 font-mono text-xs text-text-muted">
          History starts tonight · first snapshot 00:05
        </p>
      ) : (
        <>
          <div className="mb-2 flex items-center justify-between gap-4">
            <span className="label-mono text-text-muted">History</span>
            <RangeControl value={range} onChange={setRange} />
          </div>
          <CapitalChart points={points.length >= 2 ? points : all.slice(-2)} mode={mode.mode} />
        </>
      )}
    </div>
  );
}

/** 1W · 1M · 3M · All: quiet text, the current one underlined in accent. */
function RangeControl({ value, onChange }: { value: Range; onChange: (r: Range) => void }) {
  return (
    <div role="group" aria-label="Range" className="-mr-2 flex">
      {RANGES.map((r) => (
        <button
          key={r}
          type="button"
          aria-pressed={value === r}
          onClick={() => onChange(r)}
          className={`tap relative inline-flex items-center justify-center px-2 font-mono text-xs transition-colors ${
            value === r ? "text-text" : "text-text-muted hover:text-text"
          }`}
        >
          {r}
          <span
            aria-hidden
            className={`absolute inset-x-2 bottom-2 h-px transition-colors ${value === r ? "bg-accent" : "bg-transparent"}`}
          />
        </button>
      ))}
    </div>
  );
}

function Breakdown({ mode }: { mode: ModeToday }) {
  if (mode.sources.length === 0) return null;
  return (
    <div className="mt-8">
      <SectionLabel aside={`${mode.sources.length} ${mode.sources.length === 1 ? "source" : "sources"}`}>
        By source
      </SectionLabel>
      <ul data-testid="capital-breakdown" className="border-t border-border">
        {mode.sources.map((s) => (
          <SourceRow key={s.source} source={s} total={mode.total.value} live={mode.mode === "live"} />
        ))}
      </ul>
    </div>
  );
}

function SourceRow({ source, total, live }: { source: SourceToday; total: number; live: boolean }) {
  const change = changeOf(source);
  return (
    <li data-testid="source-row" data-source={source.source} className="border-b border-border py-3">
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-base text-text">{sourceLabel(source.source)}</span>
        <span data-testid="source-value" className="font-mono text-base text-text tabular-nums">
          {money(source.value)}
        </span>
      </div>
      <div className="mt-2 flex items-center gap-3">
        <span className="relative h-0.5 flex-1 overflow-hidden rounded-pill bg-surface-2">
          <span
            aria-hidden
            className={`absolute inset-y-0 left-0 w-share ${live ? "bg-live" : "bg-accent"}`}
            style={{ "--vm-share": share(source.value, total) } as React.CSSProperties}
          />
        </span>
        <span data-testid="source-change" className={`shrink-0 font-mono text-xs tabular-nums ${toneClass(change?.tone)}`}>
          {change ? signedMoney(change.cents) : "—"}
        </span>
      </div>
    </li>
  );
}

// ----------------------------------------------------------------- entries

export function EntryRow({ entry, showMode, onCorrect }: { entry: Entry; showMode: boolean; onCorrect: () => void }) {
  const cents = signedCents(entry);
  return (
    <li data-testid="entry-row" data-kind={entry.kind} data-mode={entry.mode} className="flex gap-2 border-b border-border py-3">
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-xs text-text-muted">
          <span>{entryStamp(entry.created_at)}</span>
          <span className="rounded-pill border border-border px-1.5">{entry.kind}</span>
          {showMode && <span className={entry.mode === "live" ? "text-live" : "text-on-pace"}>{entry.mode}</span>}
          {entry.pending && <span>saving</span>}
        </p>
        {entry.note && <p className="mt-1 text-sm text-text text-pretty">{entry.note}</p>}
      </div>
      <span
        data-testid="entry-amount"
        className={`shrink-0 pt-0.5 font-mono text-base tabular-nums ${cents < 0 ? "text-danger" : "text-text"}`}
      >
        {signedMoney(cents)}
      </span>
      {!entry.pending && (
        <div className="-my-2 -mr-2">
          <MoreMenu label="Entry actions" items={[{ label: "Correct with adjustment", onSelect: onCorrect }]} />
        </div>
      )}
    </li>
  );
}

export function Composer({
  ref,
  draft,
  onChange,
  onSubmit,
}: {
  ref: React.Ref<HTMLFormElement>;
  draft: Draft;
  onChange: (d: Draft) => void;
  onSubmit: (entry: Omit<Entry, "id" | "created_at" | "pending">, confirm: string) => void;
}) {
  const confirmId = useId();
  const cents = parseAmount(draft.amount);
  const liveReady = draft.mode === "paper" || draft.confirm === "LIVE";
  const signed = cents === null ? null : draft.kind === "withdrawal" || (draft.kind === "adjustment" && draft.sign === "subtract") ? -cents : cents;
  const set = (patch: Partial<Draft>) => onChange({ ...draft, ...patch });
  const confirmField = useRef<HTMLInputElement>(null);

  // Focus the confirmation when the switch flips to LIVE, not on mount.
  const lastMode = useRef(draft.mode);
  useEffect(() => {
    if (lastMode.current !== "live" && draft.mode === "live") confirmField.current?.focus();
    lastMode.current = draft.mode;
  }, [draft.mode]);

  return (
    <form
      ref={ref}
      data-testid="entry-composer"
      aria-label="New entry"
      className="mt-6 flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (cents === null || signed === null || !liveReady) return;
        onSubmit(
          {
            mode: draft.mode,
            kind: draft.kind,
            // Deposits and withdrawals are stored positive; an adjustment carries its sign.
            amount: draft.kind === "adjustment" ? signed : cents,
            note: draft.note.trim() || null,
          },
          draft.mode === "live" ? draft.confirm : "",
        );
      }}
    >
      <Chips label="Entry kind" options={KINDS} value={draft.kind} onChange={(k) => k && set({ kind: k })} />
      {draft.kind === "adjustment" && (
        <Chips
          label="Adjustment direction"
          options={["add", "subtract"] as const}
          value={draft.sign}
          onChange={(s) => s && set({ sign: s })}
          render={(s) => (s === "add" ? "+ add" : "− subtract")}
        />
      )}

      <label className="flex items-center gap-2 border-b border-border focus-within:border-accent">
        <span aria-hidden className="font-mono text-base text-text-muted">
          $
        </span>
        <input
          name="amount"
          inputMode="decimal"
          autoComplete="off"
          enterKeyHint="next"
          value={draft.amount}
          onChange={(e) => set({ amount: e.target.value })}
          placeholder="0.00"
          aria-label="Amount"
          className="tap min-w-0 flex-1 bg-transparent font-mono text-base text-text tabular-nums outline-none placeholder:text-text-muted"
        />
      </label>
      <input
        name="note"
        value={draft.note}
        maxLength={500}
        autoComplete="off"
        onChange={(e) => set({ note: e.target.value })}
        placeholder="Note (optional)"
        aria-label="Note"
        className="tap border-b border-border bg-transparent text-base text-text outline-none placeholder:text-text-muted focus:border-accent"
      />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div role="group" aria-label="Mode" className="flex rounded-pill border border-border p-0.5">
          {(["paper", "live"] as const).map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={draft.mode === m}
              onClick={() => set({ mode: m, confirm: "" })}
              className={`tap label-mono inline-flex items-center rounded-pill px-4 transition-colors ${
                draft.mode === m
                  ? m === "live"
                    ? "bg-live-fill text-live"
                    : "bg-on-pace-fill text-on-pace"
                  : "text-text-muted hover:text-text"
              }`}
            >
              {m}
            </button>
          ))}
        </div>
        <button
          type="submit"
          disabled={cents === null || !liveReady}
          className="tap rounded-pill px-4 text-sm font-medium text-accent transition-colors hover:bg-surface-2 disabled:text-text-muted disabled:hover:bg-transparent"
        >
          {signed === null ? "Add entry" : `Add ${signedMoney(signed)}`}
        </button>
      </div>

      {draft.mode === "live" && (
        <div data-testid="live-confirm" className="relative border-l-2 border-live pl-4">
          <label htmlFor={confirmId} className="block text-sm text-live">
            Live is real money. Type LIVE to record it.
          </label>
          <input
            id={confirmId}
            ref={confirmField}
            value={draft.confirm}
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            onChange={(e) => set({ confirm: e.target.value })}
            placeholder="LIVE"
            className="tap mt-1 w-full border-b border-border bg-transparent font-mono text-base text-text outline-none placeholder:text-text-muted focus:border-live"
          />
        </div>
      )}
    </form>
  );
}
