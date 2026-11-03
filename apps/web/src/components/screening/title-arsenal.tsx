"use client";

import { CheckTag, RefCardBody, RoomPill, TestTag } from "@/components/arsenal/ref-card";
import { SaveButton } from "@/components/arsenal/save-button";
import { UnverifiedReason } from "@/components/arsenal/unverified-reason";
import { RefreshWhile } from "@/components/screening/refresh-while";
import { hookBusy, type TitleHook } from "@/lib/arsenal/hook-view";
import { checkTagKind } from "@/lib/arsenal/types";

// A finished title's Arsenal cards (Chat 5 Phase 3; db/062). Finishing a
// film or show drafts 3-5 references and a Power Word from it, once ever.
// "Drafting…" while the worker writes them; then each card with its tag
// (Checking while the verifier runs, then Checked, Corrected or Unverified
// with its reason) and Save. A failed draft says so, muted. The page
// refreshes itself while anything is still being written or checked.

export function TitleArsenal({ hook }: { hook: TitleHook }) {
  return (
    <section aria-label="Arsenal" data-testid="title-arsenal" data-state={hook.status} className="mt-8">
      <h2 className="font-display text-2xl text-text">Arsenal</h2>
      {hookBusy(hook) && <RefreshWhile seconds={15} />}
      {hook.status === "queued" && (
        <p data-testid="title-arsenal-drafting" role="status" className="mt-2 text-sm text-text-muted">
          Drafting… the references this one gives you land here in a minute or two.
        </p>
      )}
      {hook.status === "failed" && (
        <div data-testid="title-arsenal-failed" className="mt-2">
          <p className="text-sm text-text-muted">No cards from this one: the draft didn&apos;t pass.</p>
          <UnverifiedReason note={hook.note} />
        </div>
      )}
      {hook.status === "drafted" && (
        <ol className="mt-3 border-t border-border">
          {hook.cards.map((c) => (
            <li key={c.id} data-testid="title-arsenal-card" data-ref-id={c.id} className="border-b border-border py-6">
              <RefCardBody c={{
                category: c.category, title: c.title, sourceWork: c.sourceWork, origin: c.origin, quote: c.quote,
                quoteBy: c.quoteBy, meaning: c.meaning, deployLine: c.deployLine, room: c.room, sourceUrl: c.sourceUrl,
                tag: checkTagKind({ verification: c.verification, status: c.status, held: c.held }),
                tagNote: c.held ? c.heldNote : c.verificationNote, isTest: c.isTest,
              }}>
                <div className="mt-3"><SaveButton refId={c.id} saved={c.saved} label={c.title} /></div>
              </RefCardBody>
            </li>
          ))}
          {hook.word && (
            <li data-testid="title-arsenal-word" data-word-id={hook.word.id} className="border-b border-border py-6">
              <div className="flex flex-wrap items-center gap-2">
                <span className="label-mono text-text-muted">Power Word</span>
                <CheckTag kind={checkTagKind({ verification: hook.word.verification, status: "pool" })}
                          note={hook.word.verificationNote} />
                {hook.word.isTest && <TestTag />}
              </div>
              <h3 data-testid="card-title" className="mt-2 font-display text-3xl leading-tight text-text">{hook.word.lemma}</h3>
              {hook.word.pronunciation && <p className="mt-1 font-mono text-sm text-text-muted">{hook.word.pronunciation}</p>}
              <p className="mt-3 text-base text-text text-pretty">{hook.word.meaning}</p>
              <p className="mt-4 border-l-2 border-accent pl-3 font-display text-2xl leading-snug text-text text-pretty">
                {hook.word.sentence}
              </p>
              <div className="mt-4"><RoomPill room={hook.word.room} /></div>
              {hook.word.verification === "unverified" && <UnverifiedReason note={hook.word.verificationNote} />}
              <div className="mt-3">
                <SaveButton wordId={hook.word.id} saved={hook.word.saved} label={hook.word.lemma} testId="word-save" />
              </div>
            </li>
          )}
        </ol>
      )}
    </section>
  );
}
