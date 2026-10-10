import type { Metadata } from "next";

import { WagsPage } from "@/components/wags/wags-page";
import { requireOwner } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";
import { loadThread, type ThreadRow } from "@/lib/wags/history";

export const metadata: Metadata = { title: "Wags" };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// /wags: every thread (a fold on phones, a column on desktop) and one
// conversation. ?t=<id> picks the thread; with none, the latest open one;
// ?t=new (or no threads yet) starts a fresh one.
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  await requireOwner();
  const supabase = await createClient();
  const { data, error } = await supabase
    .from("wags_threads")
    .select("id, title, origin_page, created_at, updated_at, archived_at")
    .order("updated_at", { ascending: false })
    .limit(200);
  if (error) throw new Error(`Couldn't read Wags threads: ${error.message}`);
  const threads = (data ?? []) as ThreadRow[];

  const t = (await searchParams).t;
  const asked = typeof t === "string" ? t : null;
  const id =
    asked && UUID.test(asked) ? asked : asked === "new" ? crypto.randomUUID() : (threads.find((x) => !x.archived_at)?.id ?? crypto.randomUUID());
  const { thread, messages } = await loadThread(supabase, id);

  return <WagsPage key={id} threads={threads} threadId={id} thread={thread} messages={messages} />;
}
