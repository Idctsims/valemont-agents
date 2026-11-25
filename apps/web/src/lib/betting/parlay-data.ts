import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import type { ParlayDay, ParlayRecord } from "./parlays";

// The Parlays page's reads (db/091): one RPC each, through the owner's session
// (RLS). Test tickets (the _e2e_parlays agent) only for a verified e2e request.

export type ParlayDetails = {
  id: number; tier: string; scope: string; sport?: string; rv: string; cohort?: number; search?: string;
  slot: string; at: number; closes?: number; mode: string;
  stake: number; pays: number; outcome?: string; pnl?: number; paid?: number;
  scored: number; fee: number; maker?: number; fair: number; fair_se?: number; naive: number; rel: number;
  pairs?: number; measured?: number; prior?: number; edge_legs?: number;
  combo?: string; combo_reason?: string; fill?: string; eq?: string; rho?: string;
  legs: { m: string; side: "yes" | "no"; e?: string; label?: string }[];
  reasons: string[];
  close?: { status: string; clv?: number; clv_pct?: number; reason?: string; age_med?: number; age_max?: number; label?: string };
};

export const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

/** Today in Chicago, as the slots count days. */
export function chicagoToday(now = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Chicago" }).format(now);
}

export async function loadParlayDay(supabase: SupabaseClient, day: string, withTests: boolean): Promise<ParlayDay> {
  const { data, error } = await supabase.rpc("parlay_day", { p_day: day, p_tests: withTests });
  if (error) throw new Error(`parlay_day: ${error.message}`);
  return data as ParlayDay;
}

export async function loadParlayRecord(supabase: SupabaseClient, withTests: boolean): Promise<ParlayRecord> {
  const { data, error } = await supabase.rpc("parlay_record", { p_tests: withTests });
  if (error) throw new Error(`parlay_record: ${error.message}`);
  return data as ParlayRecord;
}

export async function loadParlayDetails(supabase: SupabaseClient, id: number, withTests: boolean): Promise<ParlayDetails | null> {
  const { data, error } = await supabase.rpc("parlay_ticket", { p_id: id, p_tests: withTests });
  if (error) throw new Error(`parlay_ticket: ${error.message}`);
  return (data as ParlayDetails | null) ?? null;
}

/** The e2e fixtures' day: the latest day the _e2e_parlays agent committed on (test requests only). */
export async function e2eParlayDay(supabase: SupabaseClient): Promise<string | null> {
  const { data: agent } = await supabase.from("agents").select("id").eq("slug", "_e2e_parlays").maybeSingle();
  const id = (agent as { id?: number } | null)?.id;
  if (!id) return null;
  const { data } = await supabase.from("commitments").select("committed_at").eq("agent_id", id)
    .order("committed_at", { ascending: false }).limit(1).maybeSingle();
  const at = (data as { committed_at?: string } | null)?.committed_at;
  return at ? chicagoToday(new Date(at)) : null;
}
