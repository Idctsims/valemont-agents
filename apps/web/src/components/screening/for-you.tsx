"use client";

import { useEffect, useOptimistic, useState, useTransition } from "react";

import { EyeSlash, Heart, Minus, Plus } from "@phosphor-icons/react";
import Link from "next/link";

import { moveTitle, reactToTitle } from "@/app/(app)/screening/actions";
import type { ActionResult } from "@/lib/action-result";
import type { Rec } from "@/lib/screening/recs";
import { yearOf } from "@/lib/screening/types";

import { Poster } from "./search-box";

// The For you row (Chat 4 Phase 4; db/053). Each card: poster, title, year,
// "Because you loved X", "Tonight on <service>" when it streams on one of
// the owner's services, Love / Neutral / Not interested, and + Want.
// Any reaction takes the card out at once (the database excludes a reacted
// title from For you), with an Undo that clears it; + Want shelves it. The
// server render that follows each action replaces the optimistic list.

const TOAST_MS = 6000;
type Reaction = "love" | "neutral" | "not_interested";
const SAID: Record<Reaction, string> = { love: "Loved", neutral: "Marked neutral", not_interested: "Not interested" };

type Change = { type: "remove"; id: number } | { type: "restore"; rec: Rec; index: number };

function reduce(list: Rec[], c: Change): Rec[] {
  if (c.type === "remove") return list.filter((r) => r.titleId !== c.id);
  if (list.some((r) => r.titleId === c.rec.titleId)) return list;
  const next = [...list];
  next.splice(Math.min(c.index, next.length), 0, c.rec);
  return next;
}

type Toast = { rec: Rec; index: number; said: string };

export function ForYouRow({ recs }: { recs: Rec[] }) {
  const [list, change] = useOptimistic(recs, reduce);
  const [saving, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), TOAST_MS);
    return () => clearTimeout(t);
  }, [toast]);

  function run(c: Change, action: () => Promise<ActionResult<object>>) {
    startTransition(async () => {
      change(c);
      try {
        const r = await action();
        setError(r.ok ? null : r.error);
      } catch {
        setError("That didn't save. Check the connection and try again.");
      }
    });
  }

  function react(rec: Rec, reaction: Reaction) {
    const index = list.findIndex((r) => r.titleId === rec.titleId);
    setToast({ rec, index, said: SAID[reaction] });
    run({ type: "remove", id: rec.titleId }, () => reactToTitle({ titleId: rec.titleId, reaction, origin: "card" }));
  }

  function undo(t: Toast) {
    setToast(null);
    run({ type: "restore", rec: t.rec, index: t.index }, () => reactToTitle({ titleId: t.rec.titleId, reaction: "none", origin: "card" }));
  }

  function want(rec: Rec) {
    run({ type: "remove", id: rec.titleId }, () => moveTitle({ titleId: rec.titleId, shelf: "want" }));
  }

  if (list.length === 0) {
    return (
      <p data-testid="for-you-empty" className="mt-2 text-sm text-text-muted">
        Nothing new to suggest right now. The next run is tonight at 04:30.
      </p>
    );
  }

  return (
    <div aria-busy={saving} data-testid="for-you-list">
      <ul className="-mx-4 mt-3 flex snap-x gap-3 overflow-x-auto px-4 pb-2 lg:mx-0 lg:grid lg:grid-cols-4 lg:gap-x-4 lg:gap-y-6 lg:overflow-visible lg:px-0 xl:grid-cols-6">
        {list.map((r) => (
          <li key={r.titleId} data-testid="for-you-card" data-title-id={r.titleId}
              className="flex w-40 shrink-0 snap-start flex-col lg:w-auto">
            <Link href={`/screening/title/${r.kind}/${r.tmdbId}`} className="group block">
              <Poster path={r.posterPath} name={r.name} fill />
              <p className="mt-2 line-clamp-2 font-display text-lg leading-tight text-text group-hover:text-accent">{r.name}</p>
            </Link>
            <p className="label-mono mt-1 text-text-muted">
              {r.kind === "tv" ? "Series" : "Film"}
              {yearOf(r.releaseDate) ? ` · ${yearOf(r.releaseDate)}` : ""}
              {r.isTest && <span className="ml-2 rounded-pill border border-text-muted px-2 normal-case tracking-normal text-text">test</span>}
            </p>
            {r.because && (
              <p data-testid="rec-because" className="mt-1 text-sm text-text-muted">
                Because you loved <span className="text-text">{r.because}</span>
              </p>
            )}
            {r.tonight.length > 0 && (
              <p data-testid="rec-tonight" className="mt-1 text-sm text-accent">Tonight on {r.tonight[0]}</p>
            )}
            <div className="mt-auto flex items-center justify-between pt-2">
              <div role="group" aria-label={`React to ${r.name}`} className="-ml-3 flex items-center">
                <IconButton testId="rec-love" label="Love" onClick={() => react(r, "love")}><Heart size={20} aria-hidden /></IconButton>
                <IconButton testId="rec-neutral" label="Neutral" onClick={() => react(r, "neutral")}><Minus size={20} aria-hidden /></IconButton>
                <IconButton testId="rec-not-interested" label="Not interested" onClick={() => react(r, "not_interested")}>
                  <EyeSlash size={20} aria-hidden />
                </IconButton>
              </div>
              <button type="button" data-testid="rec-want" onClick={() => want(r)} aria-label={`Add ${r.name} to Want`}
                      className="tap -mr-2 inline-flex items-center gap-1 rounded-pill px-2 text-sm text-text-muted transition-colors hover:bg-surface-2 hover:text-text">
                <Plus size={16} aria-hidden /> Want
              </button>
            </div>
          </li>
        ))}
      </ul>
      {saving && <p className="label-mono mt-1 text-text-muted">saving</p>}
      {error && <p role="alert" data-testid="for-you-error" className="mt-2 text-sm text-danger">{error}</p>}
      {toast && (
        <div role="status" data-testid="for-you-toast"
             className="mt-3 inline-flex items-center gap-3 rounded-pill border border-border bg-surface-2 py-1 pl-4 pr-1 text-sm text-text">
          {toast.said}: {toast.rec.name}
          <button type="button" data-testid="for-you-undo" onClick={() => undo(toast)}
                  className="tap inline-flex items-center rounded-pill px-3 text-accent hover:bg-surface-3">Undo</button>
        </div>
      )}
    </div>
  );
}

function IconButton({ testId, label, onClick, children }: { testId: string; label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" data-testid={testId} aria-label={label} title={label} onClick={onClick}
            className="tap inline-flex items-center justify-center rounded-pill text-text-muted transition-colors hover:bg-surface-2 hover:text-text">
      {children}
    </button>
  );
}
