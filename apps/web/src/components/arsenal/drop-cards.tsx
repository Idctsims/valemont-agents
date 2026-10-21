"use client";

import { useOptimistic, useState, useTransition } from "react";

import { Brain, EyeSlash, Heart } from "@phosphor-icons/react";

import { reactToRef, reactToWord } from "@/app/(app)/arsenal/actions";
import { RefCardBody, RoomPill, TestTag } from "@/components/arsenal/ref-card";
import { SaveButton } from "@/components/arsenal/save-button";
import { Fold } from "@/components/ui/fold";
import type { ActionResult } from "@/lib/action-result";
import { checkTagKind, type Ref, type RefReaction, type Word } from "@/lib/arsenal/types";

// Today's drop (Chat 5 Phase 1; db/054): the Power Word hero, then the three
// cards in slot order. Love / Not interested / Already know it on each (the
// word too), labelled under the icon; the pressed one again takes it back.
// "Already know it" moves the card or the word into the "Already known" fold
// (with Undo). Optimistic; the server render that follows each action
// replaces it. Phase 2: Save (to My Arsenal) sits at the end of each row.

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
  const wordKnown = reactions[wordKey] === "known";

  return (
    <div aria-busy={saving} data-testid="arsenal-drop">
      {!wordKnown && <section aria-label="Power Word" data-testid="power-word" className="border-y border-border py-6 lg:max-w-3xl">
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
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <RoomPill room={word.room} />
          {word.isTest && <TestTag />}
        </div>
        <div className="flex flex-wrap items-start gap-1">
          <ReactionButtons label={word.lemma} testPrefix="word" current={reactions[wordKey] ?? null}
                           onReact={(r) => react({ kind: "word", id: word.id }, r)} />
          <div className="mt-3"><SaveButton wordId={word.id} saved={word.saved} label={word.lemma} testId="word-save" /></div>
        </div>
      </section>}

      <ol data-testid="arsenal-cards" className="lg:max-w-3xl">
        {shown.map((r) => (
          <Card key={r.id} r={r} current={reactions[key({ kind: "ref", id: r.id })] ?? null}
                onReact={(x) => react({ kind: "ref", id: r.id }, x)} />
        ))}
      </ol>

      {(known.length > 0 || wordKnown) && (
        <div className="mt-6 lg:max-w-3xl">
          <Fold label="Already known" count={known.length + (wordKnown ? 1 : 0)} testId="arsenal-known">
            {wordKnown && (
              <li data-testid="known-word" className="flex items-center justify-between gap-3 border-b border-border py-2">
                <span className="min-w-0 truncate text-sm text-text">
                  <span className="label-mono mr-2 text-text-muted">Power Word</span>{word.lemma}
                </span>
                <button type="button" data-testid="known-undo" onClick={() => react({ kind: "word", id: word.id }, null)}
                        className="tap inline-flex items-center rounded-pill px-3 text-sm text-accent hover:bg-surface-2">
                  Undo
                </button>
              </li>
            )}
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
      <RefCardBody c={{
        category: r.category, title: r.title, sourceWork: r.sourceWork, origin: r.origin, quote: r.quote,
        quoteBy: r.quoteBy, meaning: r.meaning, deployLine: r.deployLine, room: r.room, sourceUrl: r.sourceUrl,
        tag: checkTagKind({ verification: r.verification }), tagNote: r.verificationNote, isTest: r.isTest,
      }}>
        <div className="flex flex-wrap items-start gap-1">
          <ReactionButtons label={r.title} testPrefix="card" current={current} onReact={onReact} />
          <div className="mt-3"><SaveButton refId={r.id} saved={r.saved} label={r.title} /></div>
        </div>
      </RefCardBody>
    </li>
  );
}

// `label` is the accessible name; `short` is the visible one under the icon,
// and each name contains its label (voice control matches what it sees).
const BUTTONS: { r: RefReaction; label: string; short: string; Icon: typeof Heart }[] = [
  { r: "love", label: "Love", short: "Love", Icon: Heart },
  { r: "not_interested", label: "Not for me", short: "Not for me", Icon: EyeSlash },
  { r: "known", label: "Already know it", short: "Know it", Icon: Brain },
];

function ReactionButtons({ label, testPrefix, current, onReact }: {
  label: string; testPrefix: string; current: RefReaction | null; onReact: (r: RefReaction | null) => void;
}) {
  return (
    <div role="group" aria-label={`Your take on ${label}`} data-testid={`${testPrefix}-reactions`}
         data-reaction={current ?? "none"} className="-ml-2 mt-3 flex items-start gap-1">
      {BUTTONS.map(({ r, label: l, short, Icon }) => {
        const on = current === r;
        return (
          <button key={r} type="button" data-testid={`${testPrefix}-${r.replace("_", "-")}`} aria-label={l} title={l}
                  aria-pressed={on} onClick={() => onReact(on ? null : r)}
                  className={`tap inline-flex flex-col items-center justify-center gap-1 rounded-inner px-2 py-1 transition-colors ${
                    on ? "text-accent" : "text-text-muted hover:bg-surface-2 hover:text-text"
                  }`}>
            <Icon size={20} weight={on ? "fill" : "regular"} aria-hidden />
            <span aria-hidden className="font-mono text-xs leading-none whitespace-nowrap">{short}</span>
          </button>
        );
      })}
    </div>
  );
}

