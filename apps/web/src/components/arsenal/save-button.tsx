"use client";

import { useOptimistic, useState, useTransition } from "react";

import { BookmarkSimple } from "@phosphor-icons/react";

import { setSaved } from "@/app/(app)/arsenal/actions";
import { saveLabel } from "@/lib/arsenal/types";

// Save to My Arsenal (Chat 5 Phase 2; db/059 arsenal_saves). On every card
// everywhere: today's references, the Power Word, Arm Me's cards. The
// label reads "Save", then "Saved" with the icon filled (owner, Oct 21), the
// accessible name the same as what shows; aria-pressed carries the state
// too. At least 44 px. Optimistic, with the
// server's answer replacing it. Saving an Arm Me card is what sends it to
// the verifier (the database queues it), so `onChange` lets the card say
// "Checking" at once.

export function SaveButton({ refId, wordId, saved, label, testId = "card-save", onChange }: {
  refId?: number;
  wordId?: number;
  saved: boolean;
  /** What is saved, for the hover title. */
  label: string;
  testId?: string;
  onChange?: (saved: boolean) => void;
}) {
  const [state, setState] = useState(saved);
  const [shown, set] = useOptimistic(state, (_: boolean, v: boolean) => v);
  const [busy, start] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function toggle() {
    const next = !shown;
    start(async () => {
      set(next);
      try {
        const res = await setSaved({ refId, wordId, saved: next });
        if (res.ok) {
          setState(next);
          onChange?.(next);
          setError(null);
        } else {
          setError(res.error);
        }
      } catch {
        setError("That didn't save. Check the connection and try again.");
      }
    });
  }

  return (
    <span className="inline-flex flex-col">
      <button type="button" data-testid={testId} data-saved={shown} aria-pressed={shown} aria-busy={busy}
              aria-label={saveLabel(shown)} title={shown ? `${label}: saved to My Arsenal` : `Save ${label} to My Arsenal`}
              onClick={toggle}
              className={`tap inline-flex flex-col items-center justify-center gap-1 rounded-inner px-2 py-1 transition-colors ${
                shown ? "text-accent" : "text-text-muted hover:bg-surface-2 hover:text-text"
              }`}>
        <BookmarkSimple size={20} weight={shown ? "fill" : "regular"} aria-hidden />
        <span aria-hidden className="font-mono text-xs leading-none whitespace-nowrap">{saveLabel(shown)}</span>
      </button>
      {error && <span role="alert" data-testid="save-error" className="mt-1 text-xs text-danger">{error}</span>}
    </span>
  );
}
