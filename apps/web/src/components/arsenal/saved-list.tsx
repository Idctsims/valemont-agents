"use client";

import { useState, useTransition } from "react";

import { setSaved } from "@/app/(app)/arsenal/actions";
import { CheckTag, RefCardBody, RoomPill, TestTag } from "@/components/arsenal/ref-card";
import { UnverifiedReason } from "@/components/arsenal/unverified-reason";
import { ChipFace } from "@/components/ui/chip";
import type { SavedItem } from "@/lib/arsenal/saved";
import {
  CATEGORIES, CATEGORY_LABEL, checkTagKind, ROOM_LABEL, ROOMS, type Category, type Room,
} from "@/lib/arsenal/types";

// My Arsenal (Chat 5 Phase 2; docs/arsenal.md §9.4): every saved reference
// and Power Word, newest first, each with its verification tag. A saved Arm
// Me card says "Checking" until the verifier settles it, then Checked,
// Corrected, or Unverified with its reason (a held outcome conflict reads
// as Unverified, with the outcome reason). Filter by room and by category
// (a category filter shows references only: words have none). Unsave leaves
// a line with Undo, so a slip costs one tap.

const key = (i: SavedItem) => `${i.kind}:${i.id}`;

export function SavedList({ items }: { items: SavedItem[] }) {
  const [room, setRoom] = useState<Room | null>(null);
  const [category, setCategory] = useState<Category | null>(null);
  const [removed, setRemoved] = useState<Set<string>>(new Set());
  const [busy, start] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function toggle(item: SavedItem, save: boolean) {
    start(async () => {
      setRemoved((r) => {
        const next = new Set(r);
        if (save) next.delete(key(item));
        else next.add(key(item));
        return next;
      });
      try {
        const res = await setSaved(item.kind === "ref" ? { refId: item.id, saved: save } : { wordId: item.id, saved: save });
        setError(res.ok ? null : res.error);
        if (!res.ok) {
          setRemoved((r) => {
            const next = new Set(r);
            if (save) next.add(key(item));
            else next.delete(key(item));
            return next;
          });
        }
      } catch {
        setError("That didn't save. Check the connection and try again.");
      }
    });
  }

  const shown = items.filter((i) => (!room || i.room === room) && (!category || (i.kind === "ref" && i.category === category)));
  const kept = items.length - removed.size;
  return (
    <div data-testid="saved-list" aria-busy={busy} className="lg:max-w-3xl">
      <p data-testid="saved-count" className="label-mono text-text-muted">{kept} saved</p>
      <Filters label="Room" testPrefix="saved-filter-room" options={ROOMS} names={ROOM_LABEL} value={room} onChange={setRoom} />
      <Filters label="Category" testPrefix="saved-filter-category" options={CATEGORIES} names={CATEGORY_LABEL}
               value={category} onChange={setCategory} />
      {error && <p role="alert" data-testid="saved-error" className="mt-2 text-sm text-danger">{error}</p>}

      {items.length === 0 ? (
        <p data-testid="saved-empty" className="mt-6 border-t border-border pt-4 text-base text-text-muted">
          Nothing saved yet. Save a card from today&apos;s drop or from Arm Me and it lands here.
        </p>
      ) : shown.length === 0 ? (
        <p data-testid="saved-none" className="mt-6 border-t border-border pt-4 text-base text-text-muted">
          Nothing saved in that filter.
        </p>
      ) : (
        <ol className="mt-4 border-t border-border">
          {shown.map((i) => removed.has(key(i)) ? (
            <li key={key(i)} data-testid="saved-removed" data-kind={i.kind} data-id={i.id}
                className="flex items-center justify-between gap-3 border-b border-border py-2">
              <span className="min-w-0 truncate text-sm text-text-muted">
                Removed {i.kind === "ref" ? i.title : i.lemma}
              </span>
              <button type="button" data-testid="saved-undo" onClick={() => toggle(i, true)}
                      className="tap inline-flex items-center rounded-pill px-3 text-sm text-accent hover:bg-surface-2">
                Undo
              </button>
            </li>
          ) : (
            <li key={key(i)} data-testid="saved-item" data-kind={i.kind} data-id={i.id} className="border-b border-border py-6">
              {i.kind === "ref" ? (
                <RefCardBody c={{
                  category: i.category, title: i.title, sourceWork: i.sourceWork, origin: i.origin, quote: i.quote,
                  quoteBy: i.quoteBy, meaning: i.meaning, deployLine: i.deployLine, room: i.room, sourceUrl: i.sourceUrl,
                  tag: checkTagKind({ verification: i.verification, status: i.status, saved: true, held: i.held }),
                  tagNote: i.held ? i.heldNote : i.verificationNote, isTest: i.isTest,
                }}>
                  <Unsave title={i.title} onClick={() => toggle(i, false)} />
                </RefCardBody>
              ) : (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="label-mono text-text-muted">Power Word</span>
                    <CheckTag kind={checkTagKind({ verification: i.verification })} note={i.verificationNote} />
                    {i.isTest && <TestTag />}
                  </div>
                  <h3 data-testid="card-title" className="mt-2 font-display text-3xl leading-tight text-text">{i.lemma}</h3>
                  {i.pronunciation && <p className="mt-1 font-mono text-sm text-text-muted">{i.pronunciation}</p>}
                  <p className="mt-3 text-base text-text text-pretty">{i.meaning}</p>
                  <p className="mt-4 border-l-2 border-accent pl-3 font-display text-2xl leading-snug text-text text-pretty">
                    {i.sentence}
                  </p>
                  <div className="mt-4"><RoomPill room={i.room} /></div>
                  {i.verification === "unverified" && <UnverifiedReason note={i.verificationNote} />}
                  <Unsave title={i.lemma} onClick={() => toggle(i, false)} />
                </>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function Unsave({ title, onClick }: { title: string; onClick: () => void }) {
  return (
    <button type="button" data-testid="saved-unsave" onClick={onClick} title={`Remove ${title} from My Arsenal`}
            className="tap -ml-3 mt-3 inline-flex items-center rounded-pill px-3 text-sm text-text-muted hover:bg-surface-2 hover:text-text">
      Unsave
    </button>
  );
}

function Filters<T extends string>({ label, testPrefix, options, names, value, onChange }: {
  label: string;
  testPrefix: string;
  options: readonly T[];
  names: Record<T, string>;
  value: T | null;
  onChange: (v: T | null) => void;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="-ml-1 mt-2 flex flex-wrap items-center gap-x-1">
      <span className="label-mono mr-1 pl-1 text-text-muted">{label}</span>
      <button type="button" role="radio" aria-checked={value === null} data-testid={`${testPrefix}-all`}
              onClick={() => onChange(null)} className="tap group inline-flex items-center px-1">
        <ChipFace selected={value === null} mono>All</ChipFace>
      </button>
      {options.map((o) => (
        <button key={o} type="button" role="radio" aria-checked={value === o} data-testid={`${testPrefix}-${o}`}
                onClick={() => onChange(value === o ? null : o)} className="tap group inline-flex items-center px-1">
          <ChipFace selected={value === o} mono>{names[o]}</ChipFace>
        </button>
      ))}
    </div>
  );
}
