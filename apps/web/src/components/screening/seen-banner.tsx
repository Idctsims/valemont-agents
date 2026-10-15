"use client";

import { useSyncExternalStore } from "react";

import Link from "next/link";

// "Seen these?" above For you while the seen-it pass has titles left
// (Chat 4 Phase 4). "Not now" hides it on this device until more of the pass
// is answered elsewhere; the pass itself stays open from Settings.

const KEY = "vm-seen-pass-dismissed";
const listeners = new Set<() => void>();

function subscribe(fn: () => void) {
  listeners.add(fn);
  window.addEventListener("storage", fn);
  return () => {
    listeners.delete(fn);
    window.removeEventListener("storage", fn);
  };
}

function readDismissed(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null; // storage blocked: just show it
  }
}

function dismiss(left: number) {
  try {
    window.localStorage.setItem(KEY, String(left));
  } catch {
    // storage blocked: nothing to remember
  }
  listeners.forEach((fn) => fn());
}

export function SeenBanner({ left, total }: { left: number; total: number }) {
  // Hidden only when this browser dismissed it at this same count.
  const dismissedAt = useSyncExternalStore(subscribe, readDismissed, () => null);
  if (dismissedAt === String(left) || left === 0) return null;
  const started = left < total;
  return (
    <aside data-testid="seen-banner" className="mb-6 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-card border border-border bg-surface px-4 py-3 lg:max-w-3xl">
      <p className="min-w-0 flex-1 text-sm text-text">
        {started ? `${left} of ${total} left in the seen-it pass.` : `Seen these? ${total} titles near your favorites.`}
        <span className="text-text-muted"> What you&apos;ve watched never comes up as a pick.</span>
      </p>
      <div className="flex items-center gap-1">
        <Link href="/screening/seen" data-testid="seen-start"
              className="tap inline-flex items-center rounded-pill bg-accent px-4 text-sm font-medium text-on-accent hover:bg-accent/90">
          {started ? "Resume" : "Start"}
        </Link>
        <button type="button" data-testid="seen-dismiss" onClick={() => dismiss(left)}
                className="tap inline-flex items-center rounded-pill px-3 text-sm text-text-muted hover:bg-surface-2 hover:text-text">
          Not now
        </button>
      </div>
    </aside>
  );
}
