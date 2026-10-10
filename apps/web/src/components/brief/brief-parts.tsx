"use client";

import { ChatCircleText } from "@phosphor-icons/react";

import { BRIEF_CONTEXT, useWags } from "@/components/wags/wags-provider";
import { briefDay, briefTime, isLate, type Brief, type BriefSection } from "@/lib/brief/types";

// The Morning Brief's pieces, shared by Home's card, /brief and /design.
// Ledger language, not cards: a mono label, the headline in Instrument
// Serif, sections as hairline-separated rows, the one move set apart.

/** "MORNING BRIEF · 6:15 AM", with the date on /brief and the quiet tags. */
export function BriefLabel({ brief, withDate = false }: { brief: Brief; withDate?: boolean }) {
  return (
    <p data-testid="brief-label" className="label-mono flex flex-wrap items-center gap-x-2 gap-y-1 text-text-muted">
      <span>
        Morning brief{withDate ? ` · ${briefDay(brief.date)}` : ""} · {briefTime(brief.createdAt)}
      </span>
      {brief.fallback && (
        <span data-testid="brief-plain" className="rounded-pill border border-border px-2 normal-case tracking-normal">
          plain mode
        </span>
      )}
      {isLate(brief.createdAt) && (
        <span data-testid="brief-late" className="rounded-pill border border-border px-2 normal-case tracking-normal">
          late
        </span>
      )}
      {brief.isTest && (
        <span data-testid="brief-test" className="rounded-pill border border-text-muted px-2 normal-case tracking-normal text-text">
          test
        </span>
      )}
    </p>
  );
}

/** The one move: the line the whole brief points at. */
export function OneMove({ text, large = false }: { text: string; large?: boolean }) {
  return (
    <div data-testid="brief-one-move" className="border-l-2 border-accent pl-4">
      <p className="label-mono text-accent">One move</p>
      <p className={`mt-1 text-pretty text-text ${large ? "font-display text-2xl lg:text-3xl" : "text-base"}`}>{text}</p>
    </div>
  );
}

export function Sections({ sections }: { sections: BriefSection[] }) {
  return (
    <div className="flex flex-col gap-8">
      {sections.map((s) => (
        <section key={s.key} aria-label={s.title} data-testid="brief-section" data-key={s.key}>
          <h3 className="label-mono text-text-muted">{s.title}</h3>
          <ul className="mt-2 border-t border-border">
            {s.lines.map((line, i) => (
              <li key={i} className="border-b border-border py-3 text-base text-text text-pretty">
                {line}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

/** Opens Wags on the brief: "Context · Brief". */
export function AskWagsAboutThis() {
  const wags = useWags();
  return (
    <button
      type="button"
      onClick={() => wags.open(BRIEF_CONTEXT)}
      data-testid="brief-ask-wags"
      className="tap inline-flex items-center gap-2 rounded-pill px-3 text-sm text-text-muted transition-colors hover:bg-surface-2 hover:text-text"
    >
      <ChatCircleText size={18} aria-hidden />
      Ask Wags about this
    </button>
  );
}
