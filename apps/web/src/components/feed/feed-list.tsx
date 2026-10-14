"use client";

import { useEffect, useOptimistic, useState, useTransition } from "react";

import { reactToStory } from "@/app/(app)/feed/actions";
import type { ActionResult } from "@/lib/action-result";
import { cardWhen, nextReaction, type CardReaction, type FeedStory, type Pillar } from "@/lib/feed/types";

import { FeedCard } from "./feed-card";

// A feed's cards, with reactions. Taps apply at once (useOptimistic) and the
// server render that follows each action replaces the optimistic list.
//   Love             the card stays, marked; Love again takes it back (undo)
//   Neutral, Not interested
//                    the card leaves, with an Undo toast; Undo writes an
//                    'undo' row (db/039) and the card comes back
// While any action is in flight the list is aria-busy and says "saving".

/** How long the Undo toast stays. */
const TOAST_MS = 6000;

type Change =
  | { type: "react"; id: number; reaction: CardReaction }
  | { type: "restore"; story: FeedStory; index: number };

function reduce(list: FeedStory[], c: Change): FeedStory[] {
  if (c.type === "restore") {
    if (list.some((s) => s.id === c.story.id)) return list;
    const next = [...list];
    next.splice(Math.min(c.index, next.length), 0, { ...c.story, loved: false });
    return next;
  }
  if (c.reaction === "neutral" || c.reaction === "not_interested") return list.filter((s) => s.id !== c.id);
  return list.map((s) => (s.id === c.id ? { ...s, loved: c.reaction === "love" } : s));
}

type Toast = { story: FeedStory; index: number; reaction: "neutral" | "not_interested" };

export function FeedList({ stories, pillar, now, testId }: { stories: FeedStory[]; pillar: Pillar; now: number; testId: string }) {
  const [list, change] = useOptimistic(stories, reduce);
  const [saving, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), TOAST_MS);
    return () => clearTimeout(t);
  }, [toast]);

  function run(c: Change, action: () => Promise<ActionResult>) {
    startTransition(async () => {
      change(c);
      try {
        const r = await action();
        setError(r.ok ? null : r.error);
      } catch {
        // Only the network can throw here: an action reports its own
        // failures in its result (src/lib/action-result.ts).
        setError("That didn't save. Check the connection and try again.");
      }
    });
  }

  function react(story: FeedStory, tapped: "love" | "neutral" | "not_interested") {
    const reaction = nextReaction(tapped, story.loved);
    if (reaction === "neutral" || reaction === "not_interested") {
      setToast({ story, index: list.findIndex((s) => s.id === story.id), reaction });
    }
    run({ type: "react", id: story.id, reaction }, () => reactToStory({ storyId: story.id, pillar, reaction }));
  }

  function undo() {
    const t = toast;
    if (!t) return;
    setToast(null);
    run({ type: "restore", story: t.story, index: t.index }, () =>
      reactToStory({ storyId: t.story.id, pillar, reaction: "undo" }),
    );
  }

  return (
    <div aria-busy={saving} data-testid={testId}>
      {error && (
        <p role="alert" data-testid="feed-error" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}
      {list.length > 0 && (
        <ul className="mt-2 border-t border-border">
          {list.map((s) => (
            <FeedCard key={s.id} story={s} pillar={pillar} when={cardWhen(s.publishedAt, s.publishedPrecision, now)} onReact={(r) => react(s, r)} />
          ))}
        </ul>
      )}
      <p aria-live="polite" className="sr-only">
        {saving ? "Saving" : ""}
      </p>
      {toast && (
        // Phones: above the dock and clear of its raised Wags mark (-mt-5),
        // so mb-8. The pill is capped at the screen's width (w-full), so a
        // long headline truncates instead of pushing Undo off-screen (the
        // owner's 2026-10-12 run: Undo sat at x 363-430 on a 390 px phone).
        <div
          role="status"
          data-testid="feed-toast"
          className="fixed inset-x-0 bottom-dock z-30 mb-8 flex justify-center px-4 lg:bottom-6 lg:mb-0 lg:pl-rail"
        >
          <div className="flex w-full max-w-measure min-w-0 items-center gap-3 rounded-pill border border-border bg-surface py-1 pr-1 pl-5 text-sm text-text">
            <span className="min-w-0 truncate">
              {/* "Hidden" is the Not interested count's name on /settings/feed
                  (owner, 2026-10-12), so Neutral's toast never says it. */}
              {toast.reaction === "neutral" ? "Removed" : "Not interested"} · {toast.story.title}
            </span>
            <button
              type="button"
              data-testid="feed-undo"
              onClick={undo}
              className="tap shrink-0 rounded-pill px-4 font-medium text-accent transition-colors hover:bg-surface-2"
            >
              Undo
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
