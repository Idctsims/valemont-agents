"use server";

import { refresh } from "next/cache";

import { Refusal, settle, type ActionResult } from "@/lib/action-result";
import { requireOwner } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";
import { budgetState } from "@/lib/ai/budget";
import { decide, mockAllowed } from "@/lib/ai/policy";
import { WAGS_MODEL } from "@/lib/ai/pricing";
import { loadThread, type ThreadRow } from "@/lib/wags/history";
import { PROPOSAL_TOOLS, type ProposalOutcome, type WagsToolName, type WagsUIMessage } from "@/lib/wags/tools";

// Wags threads and proposal outcomes. Every action re-checks the owner and
// writes through the owner's session (db/029's RLS and column grants). The
// messages themselves are written only by /api/wags; a proposal's outcome is
// the one row written here, as a 'tool' message, append-only like the rest.

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function threadId(v: unknown): string {
  if (typeof v !== "string" || !UUID.test(v)) throw new Refusal("Unknown thread.");
  return v;
}

async function owner() {
  await requireOwner();
  return createClient();
}

/** A thread and its messages, for the sheet. An unknown id is an empty, new thread. */
export async function getThread(
  id: string,
): Promise<ActionResult<{ thread: ThreadRow | null; messages: WagsUIMessage[] }>> {
  return settle(async () => {
    const supabase = await owner();
    return loadThread(supabase, threadId(id));
  }, "Couldn't load that thread.");
}

export type WagsStatus = {
  /** "Sonnet", "Haiku", or "Mock" in local e2e. */
  model: string;
  budgetMode: boolean;
  spent: boolean;
  spentUsd: number;
  budgetUsd: number;
};

/** The footer line: which model Wags will use, and this month's spend. */
export async function getWagsStatus(): Promise<ActionResult<{ status: WagsStatus }>> {
  return settle(async () => {
    const supabase = await owner();
    const { spentUsd, budgetUsd } = await budgetState(supabase);
    const d = decide(spentUsd, budgetUsd, WAGS_MODEL, false);
    const model = mockAllowed(process.env) ? "Mock" : d.kind === "downgrade" ? "Haiku" : "Sonnet";
    return { status: { model, budgetMode: d.kind === "downgrade", spent: d.kind === "refuse", spentUsd, budgetUsd } };
  }, "Couldn't read the AI budget.");
}

export async function renameThread(id: string, title: string): Promise<ActionResult> {
  return settle(async () => {
    const supabase = await owner();
    const clean = typeof title === "string" ? title.trim().replace(/\s+/g, " ") : "";
    if (!clean || clean.length > 120) throw new Refusal("A thread title is 1 to 120 characters.");
    const { error, count } = await supabase
      .from("wags_threads")
      .update({ title: clean }, { count: "exact" })
      .eq("id", threadId(id));
    if (error) throw new Refusal(`Couldn't rename the thread: ${error.message}`);
    if (count === 0) throw new Refusal("That thread no longer exists.");
    refresh();
  });
}

export async function setThreadArchived(id: string, archived: boolean): Promise<ActionResult> {
  return settle(async () => {
    const supabase = await owner();
    const { error, count } = await supabase
      .from("wags_threads")
      .update({ archived_at: archived ? new Date().toISOString() : null }, { count: "exact" })
      .eq("id", threadId(id));
    if (error) throw new Refusal(`Couldn't ${archived ? "archive" : "restore"} the thread: ${error.message}`);
    if (count === 0) throw new Refusal("That thread no longer exists.");
    refresh();
  });
}

/** A proposal names a venture by slug; the venture actions take ids. Read-only. */
export async function resolveVenture(
  slug: string,
  workstream?: string | null,
): Promise<ActionResult<{ ventureId: string; ventureName: string; workstreamId: string | null }>> {
  return settle(async () => {
    const supabase = await owner();
    if (typeof slug !== "string" || !/^[a-z0-9]+(-[a-z0-9]+)*$/.test(slug)) throw new Refusal("Unknown venture.");
    const { data: venture, error } = await supabase
      .from("ventures")
      .select("id, name")
      .eq("slug", slug)
      .is("archived_at", null)
      .maybeSingle();
    if (error) throw new Refusal(`Couldn't find the venture: ${error.message}`);
    if (!venture) throw new Refusal(`There is no active venture "${slug}".`);
    let workstreamId: string | null = null;
    if (workstream) {
      const { data: ws, error: wsError } = await supabase
        .from("venture_workstreams")
        .select("id")
        .eq("venture_id", venture.id)
        .ilike("name", workstream.trim())
        .maybeSingle();
      if (wsError) throw new Refusal(`Couldn't find the workstream: ${wsError.message}`);
      if (!ws) throw new Refusal(`${venture.name} has no workstream "${workstream}".`);
      workstreamId = ws.id;
    }
    return { ventureId: venture.id as string, ventureName: venture.name as string, workstreamId };
  });
}

/** Confirmed or dismissed: written to the thread, so Wags reads it next turn. */
export async function recordProposalOutcome(
  thread: string,
  toolCallId: string,
  toolName: WagsToolName,
  outcome: ProposalOutcome,
): Promise<ActionResult> {
  return settle(async () => {
    const supabase = await owner();
    if (typeof toolCallId !== "string" || !toolCallId || toolCallId.length > 200) throw new Refusal("Unknown proposal.");
    if (!PROPOSAL_TOOLS.includes(toolName)) throw new Refusal("Unknown proposal.");
    if (outcome?.status !== "confirmed" && outcome?.status !== "dismissed") throw new Refusal("Unknown outcome.");
    const summary = String(outcome.summary ?? "").slice(0, 500);
    const { error } = await supabase.from("wags_messages").insert({
      thread_id: threadId(thread),
      role: "tool",
      content: `${toolName} ${outcome.status}: ${summary}`,
      parts: [{ toolCallId, toolName, output: { status: outcome.status, summary } }],
    });
    if (error) throw new Refusal(`Couldn't record that: ${error.message}`);
  });
}
