"use server";

import { refresh } from "next/cache";

import { Refusal, settle, type ActionResult } from "@/lib/action-result";
import { requireOwner } from "@/lib/auth";
import {
  LOG_KINDS,
  STAGES,
  WORKSTREAM_STATES,
  slugify,
  type LogKind,
  type Stage,
  type WorkstreamState,
} from "@/lib/ventures/types";
import { createClient } from "@/lib/supabase/server";

// Every action re-checks the owner (a Server Action is a POST to whatever
// route it lives on) and writes through the owner's session, so db/022's RLS
// applies. The running log is append-only: these actions insert into it and
// never change it; the database writes the 'auto' entries itself. Each
// returns an ActionResult and never throws an expected failure
// (src/lib/action-result.ts).

const ID = /^[0-9a-f-]{36}$/i;

function id(v: unknown, what = "item"): string {
  if (typeof v !== "string" || !ID.test(v)) throw new Refusal(`Unknown ${what}.`);
  return v;
}

function text(v: unknown, max: number, what: string, required = false): string | null {
  const t = typeof v === "string" ? v.trim() : "";
  if (!t) {
    if (required) throw new Refusal(`${what} can't be empty.`);
    return null;
  }
  if (t.length > max) throw new Refusal(`${what} is too long (${max} characters at most).`);
  return t;
}

async function owner() {
  await requireOwner();
  return createClient();
}

function check(error: { message: string } | null, what: string, count?: number | null) {
  if (error) throw new Refusal(`Couldn't ${what}: ${error.message}`);
  if (count === 0) throw new Refusal(`Couldn't ${what}: it no longer exists.`);
}

/** New venture from a name. Returns its slug (a numbered one if taken). */
export async function createVenture(name: string): Promise<ActionResult<{ slug: string }>> {
  return settle(async () => {
    const supabase = await owner();
    const clean = text(name, 120, "A venture name", true)!;
    const base = slugify(clean) || "venture";
    for (let n = 1; n <= 20; n++) {
      const slug = n === 1 ? base : `${base}-${n}`;
      const { error } = await supabase.from("ventures").insert({ name: clean, slug, sort_order: 1000 });
      if (!error) {
        refresh();
        return { slug };
      }
      if (error.code !== "23505") check(error, "add the venture");
    }
    throw new Refusal("Couldn't find a free web address for that name.");
  });
}

export type VentureFields = {
  name?: string;
  tagline?: string | null;
  role?: string | null;
  stage?: Stage | null;
  next_action?: string | null;
  blockers?: string | null;
  notes?: string | null;
};

export async function updateVenture(ventureId: string, fields: VentureFields): Promise<ActionResult> {
  return settle(async () => {
    const supabase = await owner();
    const patch: Record<string, unknown> = {};
    if ("name" in fields) patch.name = text(fields.name, 120, "The name", true);
    if ("tagline" in fields) patch.tagline = text(fields.tagline, 200, "The tagline");
    if ("role" in fields) patch.role = text(fields.role, 120, "Your role");
    if ("stage" in fields) {
      if (fields.stage !== null && !STAGES.includes(fields.stage as Stage)) throw new Refusal("Unknown stage.");
      patch.stage = fields.stage ?? null;
    }
    if ("next_action" in fields) patch.next_action = text(fields.next_action, 300, "The next action");
    if ("blockers" in fields) patch.blockers = text(fields.blockers, 2000, "Blockers");
    if ("notes" in fields) patch.notes = text(fields.notes, 20000, "Notes");
    const { error, count } = await supabase
      .from("ventures")
      .update(patch, { count: "exact" })
      .eq("id", id(ventureId, "venture"));
    check(error, "save the venture", count);
    refresh();
  });
}

export async function setVentureArchived(ventureId: string, archived: boolean): Promise<ActionResult> {
  return settle(async () => {
    const supabase = await owner();
    const { error, count } = await supabase
      .from("ventures")
      .update({ archived_at: archived ? new Date().toISOString() : null }, { count: "exact" })
      .eq("id", id(ventureId, "venture"));
    check(error, archived ? "archive the venture" : "restore the venture", count);
    refresh();
  });
}

export async function addWorkstream(ventureId: string, name: string): Promise<ActionResult> {
  return settle(async () => {
    const supabase = await owner();
    const { error } = await supabase.from("venture_workstreams").insert({
      venture_id: id(ventureId, "venture"),
      name: text(name, 120, "A workstream name", true),
      sort_order: 1000,
    });
    check(error, "add the workstream");
    refresh();
  });
}

export type WorkstreamFields = {
  name?: string;
  state?: WorkstreamState;
  next_action?: string | null;
  notes?: string | null;
};

export async function updateWorkstream(workstreamId: string, fields: WorkstreamFields): Promise<ActionResult> {
  return settle(async () => {
    const supabase = await owner();
    const patch: Record<string, unknown> = {};
    if ("name" in fields) patch.name = text(fields.name, 120, "The name", true);
    if ("state" in fields) {
      if (!WORKSTREAM_STATES.includes(fields.state as WorkstreamState)) throw new Refusal("Unknown state.");
      patch.state = fields.state;
    }
    if ("next_action" in fields) patch.next_action = text(fields.next_action, 300, "The next action");
    if ("notes" in fields) patch.notes = text(fields.notes, 20000, "Notes");
    const { error, count } = await supabase
      .from("venture_workstreams")
      .update(patch, { count: "exact" })
      .eq("id", id(workstreamId, "workstream"));
    check(error, "save the workstream", count);
    refresh();
  });
}

export async function addDate(
  ventureId: string,
  workstreamId: string | null,
  label: string,
  dueOn: string,
): Promise<ActionResult> {
  return settle(async () => {
    const supabase = await owner();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(dueOn)) throw new Refusal("Pick a date.");
    const { error } = await supabase.from("venture_dates").insert({
      venture_id: id(ventureId, "venture"),
      workstream_id: workstreamId ? id(workstreamId, "workstream") : null,
      label: text(label, 200, "A date needs a label", true),
      due_on: dueOn,
    });
    check(error, "add the date");
    refresh();
  });
}

export async function setDateDone(dateId: string, done: boolean): Promise<ActionResult> {
  return settle(async () => {
    const supabase = await owner();
    const { error, count } = await supabase
      .from("venture_dates")
      .update({ done_at: done ? new Date().toISOString() : null }, { count: "exact" })
      .eq("id", id(dateId, "date"));
    check(error, done ? "mark the date done" : "reopen the date", count);
    refresh();
  });
}

export async function addLogEntry(ventureId: string, kind: LogKind, entry: string): Promise<ActionResult> {
  return settle(async () => {
    const supabase = await owner();
    if (!LOG_KINDS.includes(kind as (typeof LOG_KINDS)[number])) throw new Refusal("Unknown entry kind.");
    const { error } = await supabase.from("venture_log").insert({
      venture_id: id(ventureId, "venture"),
      kind,
      entry: text(entry, 4000, "A log entry", true),
    });
    check(error, "add to the log");
    refresh();
  });
}
