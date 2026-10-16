"use client";

import { useOptimistic, useState, useTransition } from "react";

import { ArrowSquareOut, Brain, EyeSlash, Heart } from "@phosphor-icons/react";

import { reactToRef, reactToWord } from "@/app/(app)/arsenal/actions";
import { Fold } from "@/components/ui/fold";
import type { ActionResult } from "@/lib/action-result";
import { CATEGORY_LABEL, ROOM_LABEL, type Ref, type RefReaction, type Room, type Word } from "@/lib/arsenal/types";

// Today's drop (Chat 5 Phase 1; db/054): the Power Word hero, then the three
// cards in slot order. Love / Not interested / Already know it on each; the
// pressed one again takes it back. "Already know it" moves the card into the
// "Already known" fold (with Undo). Optimistic; the server render that
// follows each action replaces it.

type Target = { kind: "ref" | "word"; id: number };
type Reactions = Record<string, RefReaction | null>;
const key = (t: Target) => `${t.kind}:${t.id}`;

export function DropCards({ word, refs }: { word: Word; refs: Ref[] }) {
  const initial: Reactions = Object.fromEntries([
    [key({ kind: "word", id: word.id }), word.reaction],
    ...refs.map((r) => [key({ kind: "ref", id: r.id }), r.reaction]),
  ]);
  const [reactions, set] = useOptimistic(initial, (cur: Reactions, c: { k: string; r: RefReaction | null }) => ({ ...cur, [c.k]: c.r }));
  const [saving, start] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function react(t: Target, r: RefReaction | null) {
    start(async () => {
      set({ k: key(t), r });
      try {
        const res: ActionResult = t.kind === "ref"
          ? await reactToRef({ refId: t.id, reaction: r ?? "none" })
          : await reactToWord({ wordId: t.id, reaction: r ?? "none" });
        setError(res.ok ? null : res.error);
      } catch {
        setError("That didn't save. Check the connection and try again.");
      }
    });
  }

  const shown = refs.filter((r) => reactions[key({ kind: "ref", id: r.id })] !== "known");
  const known = refs.filter((r) => reactions[key({ kind: "ref", id: r.id })] === "known");
  const wordKey = key({ kind: "word", id: word.id });

  return (
    <div aria-busy={saving} data-testid="arsenal-drop">
      <section aria-label="Power Word" data-testid="power-word" className="border-y border-border py-6 lg:max-w-3xl">
        <p className="label-mono text-text-muted">Power Word</p>
        <h2 data-testid="power-word-lemma" className="mt-2 font-display text-4xl leading-none text-text">{word.lemma}</h2>
        {word.pronunciation && (
          <p data-testid="power-word-ipa" className="mt-2 font-mono text-sm text-text-muted">{word.pronunciation}</p>
        )}
        <p className="mt-4 text-lg text-text text-pretty">{word.meaning}</p>
        <p data-testid="power-word-sentence"
           className="mt-4 border-l-2 border-accent pl-3 font-display text-2xl leading-snug text-text text-pretty">
          {word.sentence}
        </p>
        {word.etymology && (
          <p data-testid="power-word-etymology" className="mt-4 text-sm text-text-muted text-pretty">
            {word.etymology}
            {word.etymologyUrl && (
              <a href={word.etymologyUrl} target="_blank" rel="noopener noreferrer"
                 className="ml-1 underline decoration-border underline-offset-4 hover:text-text">Wiktionary</a>
            )}
          </p>
        )}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <RoomPill room={word.room} />
            {word.isTest && <TestTag />}
          </div>
          <ReactionButtons label={word.lemma} testPrefix="word" current={reactions[wordKey] ?? null}
                           onReact={(r) => react({ kind: "word", id: word.id }, r)} />
        </div>
      </section>

      <ol data-testid="arsenal-cards" className="lg:max-w-3xl">
        {shown.map((r) => (
          <Card key={r.id} r={r} current={reactions[key({ kind: "ref", id: r.id })] ?? null}
                onReact={(x) => react({ kind: "ref", id: r.id }, x)} />
        ))}
      </ol>

      {known.length > 0 && (
        <div className="mt-6 lg:max-w-3xl">
          <Fold label="Already known" count={known.length} testId="arsenal-known">
            {known.map((r) => (
              <li key={r.id} data-testid="known-ref" className="flex items-center justify-between gap-3 border-b border-border py-2">
                <span className="min-w-0 truncate text-sm text-text">{r.title}</span>
                <button type="button" data-testid="known-undo" onClick={() => react({ kind: "ref", id: r.id }, null)}
                        className="tap inline-flex items-center rounded-pill px-3 text-sm text-accent hover:bg-surface-2">
                  Undo
                </button>
              </li>
            ))}
          </Fold>
        </div>
      )}
      {saving && <p className="label-mono mt-2 text-text-muted">saving</p>}
      {error && <p role="alert" data-testid="arsenal-error" className="mt-2 text-sm text-danger">{error}</p>}
    </div>
  );
}

function Card({ r, current, onReact }: { r: Ref; current: RefReaction | null; onReact: (x: RefReaction | null) => void }) {
  return (
    <li data-testid="arsenal-card" data-ref-id={r.id} data-category={r.category} className="border-b border-border py-6">
      <div className="flex flex-wrap items-center gap-2">
        <span data-testid="card-category" className="label-mono text-text-muted">{CATEGORY_LABEL[r.category]}</span>
        <CheckTag verification={r.verification} note={r.verificationNote} />
        {r.isTest && <TestTag />}
      </div>
      <h3 data-testid="card-title" className="mt-2 font-display text-3xl leading-tight text-text text-balance">{r.title}</h3>
      <p data-testid="card-source-work" className="mt-1 text-sm italic text-text-muted">{r.sourceWork}</p>
      <p data-testid="card-origin" className="mt-3 text-base text-text-muted text-pretty">{r.origin}</p>
      {r.quote && (
        <p data-testid="card-quote" className="mt-3 text-base text-text text-pretty">
          “{r.quote}”{r.quoteBy && <span className="text-text-muted"> — {r.quoteBy}</span>}
        </p>
      )}
      <p data-testid="card-meaning" className="mt-3 text-base text-text text-pretty">{r.meaning}</p>
      <p data-testid="card-deploy"
         className="mt-4 border-l-2 border-accent pl-3 font-display text-2xl leading-snug text-text text-pretty">
        {r.deployLine}
      </p>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <RoomPill room={r.room} />
          {r.sourceUrl && (
            <a href={r.sourceUrl} target="_blank" rel="noopener noreferrer" data-testid="card-source"
               className="tap inline-flex items-center gap-1 rounded-pill px-3 text-sm text-text-muted hover:bg-surface-2 hover:text-text">
              Source <ArrowSquareOut size={14} aria-hidden />
            </a>
          )}
        </div>
        <ReactionButtons label={r.title} testPrefix="card" current={current} onReact={onReact} known />
      </div>
    </li>
  );
}

const BUTTONS: { r: RefReaction; label: string; Icon: typeof Heart }[] = [
  { r: "love", label: "Love", Icon: Heart },
  { r: "not_interested", label: "Not interested", Icon: EyeSlash },
  { r: "known", label: "Already know it", Icon: Brain },
];

function ReactionButtons({ label, testPrefix, current, onReact, known = false }: {
  label: string; testPrefix: string; current: RefReaction | null; onReact: (r: RefReaction | null) => void; known?: boolean;
}) {
  return (
    <div role="group" aria-label={`Your take on ${label}`} data-testid={`${testPrefix}-reactions`}
         data-reaction={current ?? "none"} className="-mr-2 flex items-center">
      {BUTTONS.filter((b) => known || b.r !== "known").map(({ r, label: l, Icon }) => {
        const on = current === r;
        return (
          <button key={r} type="button" data-testid={`${testPrefix}-${r.replace("_", "-")}`} aria-label={l} title={l}
                  aria-pressed={on} onClick={() => onReact(on ? null : r)}
                  className={`tap inline-flex items-center justify-center rounded-pill transition-colors ${
                    on ? "text-accent" : "text-text-muted hover:bg-surface-2 hover:text-text"
                  }`}>
            <Icon size={20} weight={on ? "fill" : "regular"} aria-hidden />
          </button>
        );
      })}
    </div>
  );
}

function RoomPill({ room }: { room: Room }) {
  return (
    <span data-testid="room-chip"
          className="inline-flex h-8 items-center rounded-pill border border-border px-3 font-mono text-xs text-text">
      {ROOM_LABEL[room]}
    </span>
  );
}

function CheckTag({ verification, note }: { verification: Ref["verification"]; note: string | null }) {
  if (verification !== "verified" && verification !== "corrected") return null;
  const corrected = verification === "corrected";
  return (
    <span data-testid="card-check" title={note ?? undefined}
          className={`label-mono inline-flex h-6 items-center rounded-pill border px-2 normal-case tracking-normal ${
            corrected ? "border-accent text-accent" : "border-text-muted text-text"
          }`}>
      {corrected ? "Corrected" : "Checked"}
    </span>
  );
}

function TestTag() {
  return <span className="label-mono rounded-pill border border-text-muted px-2 normal-case tracking-normal text-text">test</span>;
}
