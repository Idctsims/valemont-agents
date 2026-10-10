"use client";

import { ArrowSquareOut, NotePencil, X } from "@phosphor-icons/react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { getThread } from "@/app/(app)/wags/actions";
import type { PageContext } from "@/lib/wags/page-context";
import type { WagsUIMessage } from "@/lib/wags/tools";

import { Conversation } from "./conversation";
import { Refusal } from "./parts";

// The sheet (phones: about 92% of the screen from the bottom, drag the handle
// down to dismiss) and the panel (desktop: the right edge, full height). One
// component, two layouts by breakpoint. Loaded on first open only.

const DISMISS_PX = 112;

export function WagsSheet({
  threadId,
  page,
  onRemovePage,
  onClose,
  onNewThread,
  onStarted,
}: {
  threadId: string;
  page: PageContext | null;
  onRemovePage: () => void;
  onClose: () => void;
  onNewThread: () => void;
  /** Called once a thread has messages, so reopening the sheet continues it. */
  onStarted: (id: string) => void;
}) {
  const [loaded, setLoaded] = useState<{ id: string; title: string | null; messages: WagsUIMessage[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const panel = useRef<HTMLDivElement>(null);

  const load = useCallback(
    (id: string) =>
      getThread(id)
        .then((r) => {
          setError(r.ok ? null : r.error);
          if (r.ok) setLoaded({ id, title: r.thread?.title ?? null, messages: r.messages });
        })
        .catch(() => setError("Wags couldn't be reached. Check the connection and try again.")),
    [],
  );

  useEffect(() => {
    void load(threadId);
  }, [threadId, load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    // Phones: the page under the sheet must not scroll with it.
    const phone = window.matchMedia("(max-width: 63.99rem)").matches;
    const prior = document.body.style.overflow;
    if (phone) document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prior;
    };
  }, [onClose]);

  // Drag the handle down to dismiss (phones). The sheet follows the finger;
  // past DISMISS_PX it closes, short of it it springs back.
  const drag = useRef<{ y: number; id: number } | null>(null);
  // A transform only while dragging: a standing one put the sheet on its own
  // layer, and at fractional device-pixel ratios (2.625 on a Pixel 7) a
  // one-pixel seam at the bottom let the page behind show through.
  const setDrag = (px: number) => {
    const el = panel.current;
    if (el) el.style.transform = px > 0 ? `translateY(${px}px)` : "";
  };
  const handle = {
    onPointerDown: (e: React.PointerEvent) => {
      drag.current = { y: e.clientY, id: e.pointerId };
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    },
    onPointerMove: (e: React.PointerEvent) => {
      if (drag.current?.id === e.pointerId) setDrag(e.clientY - drag.current.y);
    },
    onPointerUp: (e: React.PointerEvent) => {
      if (drag.current?.id !== e.pointerId) return;
      const dy = e.clientY - drag.current.y;
      drag.current = null;
      if (dy > DISMISS_PX) onClose();
      else setDrag(0);
    },
    onPointerCancel: () => {
      drag.current = null;
      setDrag(0);
    },
  };

  const title = loaded?.id === threadId && loaded.title ? loaded.title : "New thread";

  return (
    <div className="fixed inset-0 z-50 lg:pointer-events-none">
      <button
        type="button"
        aria-label="Close Wags"
        tabIndex={-1}
        onClick={onClose}
        className="absolute inset-0 h-full w-full bg-bg/70 lg:hidden"
      />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label="Wags"
        data-testid="wags-sheet"
        className="absolute inset-x-0 -bottom-px flex h-sheet flex-col overflow-hidden rounded-t-card border-t border-border bg-surface pb-safe lg:pointer-events-auto lg:inset-y-0 lg:right-0 lg:left-auto lg:h-full lg:w-panel lg:rounded-none lg:border-t-0 lg:border-l lg:pb-0"
      >
        <div {...handle} className="flex cursor-grab touch-none justify-center pt-2 pb-1 lg:hidden" aria-hidden>
          <span className="h-1 w-10 rounded-pill bg-surface-3" />
        </div>
        <header className="flex items-center gap-1 border-b border-border px-safe pb-2 lg:px-6 lg:pt-4">
          <div className="min-w-0 flex-1">
            <p className="label-mono text-accent">Wags</p>
            <p data-testid="wags-thread-title" className="truncate text-sm text-text">
              {title}
            </p>
          </div>
          <button type="button" onClick={onNewThread} aria-label="New thread" className="tap inline-flex items-center justify-center rounded-pill text-text-muted transition-colors hover:bg-surface-2 hover:text-text">
            <NotePencil size={20} aria-hidden />
          </button>
          <Link
            href={`/wags?t=${threadId}`}
            aria-label="Open in Wags"
            className="tap inline-flex items-center justify-center rounded-pill text-text-muted transition-colors hover:bg-surface-2 hover:text-text"
          >
            <ArrowSquareOut size={20} aria-hidden />
          </Link>
          <button type="button" onClick={onClose} aria-label="Close" className="tap -mr-2 inline-flex items-center justify-center rounded-pill text-text-muted transition-colors hover:bg-surface-2 hover:text-text">
            <X size={20} aria-hidden />
          </button>
        </header>

        {error ? (
          <div className="px-safe lg:px-6">
            <Refusal message={error} />
          </div>
        ) : loaded?.id === threadId ? (
          <Conversation
            key={threadId}
            threadId={threadId}
            initialMessages={loaded.messages}
            page={page}
            onRemovePage={onRemovePage}
            autoFocus
            onAnswered={() => {
              onStarted(threadId);
              void load(threadId);
            }}
          />
        ) : (
          <p className="label-mono px-safe pt-6 text-text-muted lg:px-6">Opening…</p>
        )}
      </div>
    </div>
  );
}
