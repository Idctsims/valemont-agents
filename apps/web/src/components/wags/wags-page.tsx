"use client";

import { CaretRight, NotePencil } from "@phosphor-icons/react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { renameThread, setThreadArchived } from "@/app/(app)/wags/actions";
import { Fold } from "@/components/ui/fold";
import { MoreMenu } from "@/components/ventures/parts";
import type { ThreadRow } from "@/lib/wags/thread";
import type { WagsUIMessage } from "@/lib/wags/tools";

import { Conversation } from "./conversation";
import { Refusal } from "./parts";

// The full Wags page. Phones: the thread list folds above the conversation.
// Desktop: a column of threads beside it. Rename and archive live in each
// thread's ⋯ menu; archived threads fold away at the end of the list.

function when(iso: string): string {
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "America/Chicago" }).format(new Date(iso));
}

export function WagsPage({
  threads,
  threadId,
  thread,
  messages,
}: {
  threads: ThreadRow[];
  threadId: string;
  thread: ThreadRow | null;
  messages: WagsUIMessage[];
}) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [showThreads, setShowThreads] = useState(false);
  const [, startTransition] = useTransition();
  const open = threads.filter((t) => !t.archived_at);
  const archived = threads.filter((t) => t.archived_at);
  const title = thread?.title ?? (messages.length ? "Untitled thread" : "New thread");

  function act(run: () => Promise<{ ok: boolean; error?: string }>) {
    startTransition(async () => {
      try {
        const r = await run();
        setError(r.ok ? null : (r.error ?? "That didn't save."));
      } catch {
        setError("That didn't save. Check the connection and try again.");
      }
    });
  }

  const row = (t: ThreadRow) => (
    <li key={t.id} data-testid="wags-thread" data-thread={t.id} data-archived={t.archived_at ? "true" : "false"} className="flex items-center gap-1 border-b border-border">
      {renaming === t.id ? (
        <form
          className="flex-1 py-1"
          onSubmit={(e) => {
            e.preventDefault();
            const value = new FormData(e.currentTarget).get("title");
            setRenaming(null);
            act(() => renameThread(t.id, String(value ?? "")));
          }}
        >
          <label className="sr-only" htmlFor={`rename-${t.id}`}>
            Thread title
          </label>
          <input
            id={`rename-${t.id}`}
            name="title"
            defaultValue={t.title ?? ""}
            maxLength={120}
            autoFocus
            onBlur={(e) => e.currentTarget.form?.requestSubmit()}
            onKeyDown={(e) => e.key === "Escape" && setRenaming(null)}
            className="min-h-11 w-full border-b border-accent bg-transparent text-sm text-text focus:outline-none"
          />
        </form>
      ) : (
        <Link
          href={`/wags?t=${t.id}`}
          aria-current={t.id === threadId ? "page" : undefined}
          className={`tap flex min-w-0 flex-1 items-center justify-between gap-3 text-sm transition-colors ${
            t.id === threadId ? "font-medium text-accent" : "text-text hover:text-accent"
          }`}
        >
          <span className="truncate">{t.title ?? "Untitled thread"}</span>
          <span className="shrink-0 font-mono text-xs text-text-muted">{when(t.updated_at)}</span>
        </Link>
      )}
      <MoreMenu
        label={`Actions for ${t.title ?? "thread"}`}
        items={[
          { label: "Rename", onSelect: () => setRenaming(t.id) },
          t.archived_at
            ? { label: "Restore", onSelect: () => act(() => setThreadArchived(t.id, false)) }
            : { label: "Archive", onSelect: () => act(() => setThreadArchived(t.id, true)) },
        ]}
      />
    </li>
  );

  const list = (
    <>
      <ul data-testid="wags-threads" className="border-t border-border">
        {open.length ? open.map(row) : <li className="py-3 text-sm text-text-muted">No threads yet.</li>}
      </ul>
      {archived.length > 0 && (
        <Fold label="Archived" count={archived.length} testId="wags-archived">
          {archived.map(row)}
        </Fold>
      )}
    </>
  );

  return (
    <div data-testid="wags-page" className="flex h-wags-page flex-col lg:flex-row lg:gap-10">
      <aside className="shrink-0 lg:w-64 lg:overflow-y-auto">
        <div className="flex items-center justify-between">
          <h1 className="font-display text-4xl text-text">Wags</h1>
          <Link href="/wags?t=new" aria-label="New thread" className="tap -mr-2 inline-flex items-center justify-center rounded-pill text-text-muted transition-colors hover:bg-surface-2 hover:text-text">
            <NotePencil size={22} aria-hidden />
          </Link>
        </div>
        {error && <Refusal message={error} />}
        {/* One list, rendered once: a fold on phones, always open on desktop. */}
        <button
          type="button"
          aria-expanded={showThreads}
          aria-controls="wags-thread-list"
          onClick={() => setShowThreads((o) => !o)}
          className="tap mt-3 flex w-full items-center justify-between border-b border-border font-mono text-xs text-text-muted transition-colors hover:text-text lg:hidden"
        >
          <span>Threads · {open.length}</span>
          <CaretRight size={16} aria-hidden className={`transition-transform ${showThreads ? "rotate-90" : ""}`} />
        </button>
        <div id="wags-thread-list" className={`mt-3 lg:mt-4 lg:block ${showThreads ? "block" : "hidden"}`}>
          {list}
        </div>
      </aside>

      <section aria-label="Conversation" className="-mx-4 mt-3 flex min-h-0 flex-1 flex-col border-t border-border lg:mx-0 lg:mt-0 lg:rounded-card lg:border">
        <p data-testid="wags-thread-title" className="label-mono truncate px-safe pt-3 text-text-muted lg:px-6">
          {title}
        </p>
        <Conversation
          threadId={threadId}
          initialMessages={messages}
          page={null}
          onAnswered={() => {
            // The first answer creates the thread (and titles it); show it in the list.
            if (!thread || !thread.title) router.replace(`/wags?t=${threadId}`, { scroll: false });
          }}
        />
      </section>
    </div>
  );
}
