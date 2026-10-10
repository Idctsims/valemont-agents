"use client";

import Link from "next/link";
import { useOptimistic, useState, useTransition } from "react";

import { setDateDone } from "@/app/(app)/ventures/actions";
import type { TodayRow } from "@/lib/ventures/types";

/**
 * Home's TODAY: venture dates due today or earlier (v_venture_today). Shown
 * only when there are any. The ring marks a date done; the row leaves at
 * once and the server render that follows confirms it. A failed save brings
 * it back with the error.
 */
export function TodayModule({ rows }: { rows: TodayRow[] }) {
  const [list, remove] = useOptimistic(rows, (current, id: string) => current.filter((r) => r.id !== id));
  const [saving, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  if (rows.length === 0 && !error) return null;

  function done(row: TodayRow) {
    startTransition(async () => {
      remove(row.id);
      try {
        const r = await setDateDone(row.id, true);
        setError(r.ok ? null : r.error);
      } catch {
        // Only the network can throw here: an action reports its own
        // failures in its result (src/lib/action-result.ts).
        setError("That didn't save. Check the connection and try again.");
      }
    });
  }

  return (
    <section aria-label="Today" aria-busy={saving} data-testid="venture-today" className="mb-12">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="label-mono text-text-muted">Today</h2>
        <p className="font-mono text-xs text-text-muted">
          {saving && <span className="mr-3">saving</span>}
          {list.length} due
        </p>
      </div>
      <ul className="mt-3 border-t border-border">
        {list.map((r) => (
          <TodayItem key={r.id} row={r} onDone={() => done(r)} />
        ))}
      </ul>
      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}
    </section>
  );
}

export function TodayItem({ row, onDone }: { row: TodayRow; onDone?: () => void }) {
  const where = [row.venture_name, row.workstream_name].filter(Boolean).join(" · ");
  return (
    <li data-testid="today-row" className="flex items-start gap-2 border-b border-border py-2">
      {onDone ? (
        <button
          type="button"
          role="checkbox"
          aria-checked={false}
          aria-label={`Mark ${row.label} done`}
          onClick={onDone}
          className="tap inline-flex shrink-0 items-center justify-center rounded-pill transition-colors hover:bg-surface-2"
        >
          <span className="size-6 rounded-pill border-2 border-text-muted" />
        </button>
      ) : (
        <span className="tap inline-flex shrink-0 items-center justify-center">
          <span className="size-6 rounded-pill border-2 border-text-muted" />
        </span>
      )}
      <Link href={`/ventures/${row.venture_slug}`} className="min-w-0 flex-1 py-1.5">
        <span className="block text-base text-text text-pretty">{row.label}</span>
        <span className="mt-1 flex flex-wrap gap-x-3 font-mono text-xs text-text-muted">
          <span>{where}</span>
          {row.overdue && <span className="text-danger">overdue</span>}
        </span>
      </Link>
    </li>
  );
}
