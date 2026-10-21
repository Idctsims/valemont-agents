import { ArrowSquareOut } from "@phosphor-icons/react";

import { UnverifiedReason } from "@/components/arsenal/unverified-reason";
import {
  CATEGORY_LABEL, CHECK_TAG_LABEL, ROOM_LABEL, quoteCredit, type Category, type CheckTagKind, type Room,
} from "@/lib/arsenal/types";

// One Arsenal reference card (Chat 5): the category and its tag, the
// title, the source work, where it comes from, the quote with its credit,
// what it means, the line to deploy, the room and the Source link. Shared
// by today's drop, Arm Me and My Arsenal, so a card reads the same
// wherever it shows. `children` is the card's action row (reactions,
// Save). An Unverified card shows its reason, muted (never danger red).

export type CardData = {
  category: Category;
  title: string;
  sourceWork: string;
  origin: string;
  quote: string | null;
  quoteBy: string | null;
  meaning: string;
  deployLine: string;
  room: Room;
  sourceUrl: string | null;
  tag: CheckTagKind;
  tagNote?: string | null;
  isTest: boolean;
  /** Arm Me: a seed, a dropped or a saved reference. */
  fromArsenal?: boolean;
  /** Say "No source given" when there is no Source link (Arm Me). */
  sayNoSource?: boolean;
};

export function RefCardBody({ c, children }: { c: CardData; children?: React.ReactNode }) {
  const credit = quoteCredit(c.category, c.quoteBy, c.sourceWork);
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <span data-testid="card-category" className="label-mono text-text-muted">{CATEGORY_LABEL[c.category]}</span>
        <CheckTag kind={c.tag} note={c.tagNote ?? null} />
        {c.fromArsenal && (
          <span data-testid="arm-from-arsenal" className="label-mono normal-case tracking-normal text-accent">
            From your Arsenal
          </span>
        )}
        {c.isTest && <TestTag />}
      </div>
      <h3 data-testid="card-title" className="mt-2 font-display text-3xl leading-tight text-text text-balance">{c.title}</h3>
      <p data-testid="card-source-work" className="mt-1 text-sm italic text-text-muted">{c.sourceWork}</p>
      <p data-testid="card-origin" className="mt-3 text-base text-text-muted text-pretty">{c.origin}</p>
      {c.quote && (
        <p data-testid="card-quote" className="mt-3 text-base text-text text-pretty">
          “{c.quote}”{credit && <span data-testid="card-quote-by" className="text-text-muted"> — {credit}</span>}
        </p>
      )}
      <p data-testid="card-meaning" className="mt-3 text-base text-text text-pretty">{c.meaning}</p>
      <p data-testid="card-deploy"
         className="mt-4 border-l-2 border-accent pl-3 font-display text-2xl leading-snug text-text text-pretty">
        {c.deployLine}
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <RoomPill room={c.room} />
        {c.sourceUrl && (
          <a href={c.sourceUrl} target="_blank" rel="noopener noreferrer" data-testid="card-source"
             className="tap inline-flex items-center gap-1 rounded-pill px-3 text-sm text-text-muted hover:bg-surface-2 hover:text-text">
            Source <ArrowSquareOut size={14} aria-hidden />
          </a>
        )}
        {!c.sourceUrl && c.sayNoSource && (
          <span data-testid="card-no-source" className="px-3 text-sm text-text-muted">No source given</span>
        )}
      </div>
      {c.tag === "unverified" && c.tagNote && <UnverifiedReason note={c.tagNote} />}
      {children}
    </>
  );
}

export function RoomPill({ room }: { room: Room }) {
  return (
    <span data-testid="room-chip"
          className="inline-flex h-8 items-center rounded-pill border border-border px-3 font-mono text-xs text-text">
      {ROOM_LABEL[room]}
    </span>
  );
}

/** Checked and Corrected are the verifier's; the rest say what is not done yet. */
export function CheckTag({ kind, note }: { kind: CheckTagKind; note: string | null }) {
  if (!kind) return null;
  const tone =
    kind === "corrected" ? "border-accent text-accent"
      : kind === "checked" ? "border-text-muted text-text"
        : "border-border text-text-muted";
  return (
    <span data-testid="card-check" data-tag={kind} title={note ?? undefined}
          className={`label-mono inline-flex h-6 items-center rounded-pill border px-2 normal-case tracking-normal ${tone}`}>
      {CHECK_TAG_LABEL[kind]}
    </span>
  );
}

export function TestTag() {
  return <span className="label-mono rounded-pill border border-text-muted px-2 normal-case tracking-normal text-text">test</span>;
}
