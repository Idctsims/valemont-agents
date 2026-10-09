import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import { MODES, toCents, type Entry, type Mode, type ModeToday } from "./types";

// Reads run with the owner's session, so db/026's RLS applies. Every number
// comes from the database's capital_value_as_of (through v_capital_today) or
// from capital_snapshots, which the worker writes from the same function.

function fail(what: string, error: { message: string } | null): never {
  throw new Error(`Couldn't read ${what}: ${error?.message ?? "unknown error"}`);
}

export type CapitalData = {
  /** Modes that have data, paper first. Live appears only once live rows exist. */
  modes: ModeToday[];
  /** Per mode: each snapshot row's date and value (cents), oldest first. */
  snapshots: Record<Mode, { snap_date: string; value: number }[]>;
  /** Newest first. */
  entries: Entry[];
};

export const ENTRY_LIMIT = 200;

export async function capitalData(supabase: SupabaseClient): Promise<CapitalData> {
  const [t, s, e] = await Promise.all([
    supabase.from("v_capital_today").select("mode, is_total, source, value, prior_value"),
    supabase.from("capital_snapshots").select("snap_date, mode, value").order("snap_date"),
    supabase
      .from("bankroll_entries")
      .select("id, mode, kind, amount, note, created_at")
      .order("id", { ascending: false })
      .limit(ENTRY_LIMIT),
  ]);
  if (t.error) fail("today's capital", t.error);
  if (s.error) fail("capital history", s.error);
  if (e.error) fail("bankroll entries", e.error);

  const modes: ModeToday[] = [];
  for (const mode of MODES) {
    const rows = t.data.filter((r) => r.mode === mode);
    const total = rows.find((r) => r.is_total);
    if (!total) continue;
    const toSource = (r: (typeof rows)[number]) => ({
      source: r.source as string,
      value: toCents(r.value),
      prior: r.prior_value === null ? null : toCents(r.prior_value),
    });
    modes.push({
      mode,
      total: toSource(total),
      sources: rows
        .filter((r) => !r.is_total)
        .map(toSource)
        .sort((a, b) => b.value - a.value || a.source.localeCompare(b.source)),
    });
  }

  const snapshots: CapitalData["snapshots"] = { paper: [], live: [] };
  for (const r of s.data) snapshots[r.mode as Mode].push({ snap_date: r.snap_date, value: toCents(r.value) });

  return {
    modes,
    snapshots,
    entries: e.data.map((r) => ({
      id: String(r.id),
      mode: r.mode as Mode,
      kind: r.kind as Entry["kind"],
      amount: toCents(r.amount),
      note: r.note,
      created_at: r.created_at,
    })),
  };
}
