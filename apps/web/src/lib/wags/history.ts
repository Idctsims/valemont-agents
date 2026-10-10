import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import { rowsToMessages, type MessageRow, type ThreadRow } from "./thread";
import type { WagsUIMessage } from "./tools";

export { forModel, rowsToMessages, type MessageRow, type ThreadRow } from "./thread";

// A thread, as stored (db/029 wags_messages, append-only), turned back into
// AI SDK UI messages. The database is the source of truth: the route builds
// the conversation from these rows, never from messages a client sends.
// The row-to-message rules are in ./thread.ts.

export async function loadThread(
  supabase: SupabaseClient,
  threadId: string,
): Promise<{ thread: ThreadRow | null; messages: WagsUIMessage[] }> {
  const [t, m] = await Promise.all([
    supabase
      .from("wags_threads")
      .select("id, title, origin_page, created_at, updated_at, archived_at")
      .eq("id", threadId)
      .maybeSingle(),
    supabase
      .from("wags_messages")
      .select("id, role, content, parts, model, incomplete, cost_usd, created_at")
      .eq("thread_id", threadId)
      .order("id"),
  ]);
  if (t.error) throw new Error(`Couldn't read the thread: ${t.error.message}`);
  if (m.error) throw new Error(`Couldn't read the thread's messages: ${m.error.message}`);
  return { thread: t.data as ThreadRow | null, messages: rowsToMessages((m.data ?? []) as MessageRow[]) };
}
