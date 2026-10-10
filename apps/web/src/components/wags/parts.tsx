"use client";

import { ArrowDown, X } from "@phosphor-icons/react";
import { Streamdown, type Components } from "streamdown";

import { button } from "@/components/ui/button";
import type { ProposalOutcome, WagsToolName } from "@/lib/wags/tools";

// Wags's pieces, with no data hooks, so /design can render every state from
// samples. Editorial, not chat bubbles: each turn is a small mono label and a
// paragraph; exchanges are separated by a hairline.

// ------------------------------------------------------------------ markdown

const md: Components = {
  p: ({ children }) => <p className="mt-3 first:mt-0">{children}</p>,
  ul: ({ children }) => <ul className="mt-3 list-disc space-y-1 pl-5 first:mt-0 marker:text-text-muted">{children}</ul>,
  ol: ({ children }) => <ol className="mt-3 list-decimal space-y-1 pl-5 first:mt-0 marker:text-text-muted">{children}</ol>,
  li: ({ children }) => <li className="pl-1">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-text">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  code: ({ children }) => <code className="rounded-inner bg-surface-2 px-1.5 font-mono text-sm">{children}</code>,
  pre: ({ children }) => <pre className="mt-3 overflow-x-auto rounded-inner bg-surface-2 p-3 font-mono text-sm">{children}</pre>,
  a: ({ children, href }) => (
    <a href={href} className="text-accent underline underline-offset-2" target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  ),
};

// Prose only: no raw HTML, no tables, no images, no headings (a heading in a
// reply would compete with the page's one hero).
const ALLOWED = ["p", "ul", "ol", "li", "strong", "em", "code", "pre", "a", "br"];

export function Markdown({ text, streaming = false }: { text: string; streaming?: boolean }) {
  return (
    <Streamdown
      mode={streaming ? "streaming" : "static"}
      isAnimating={streaming}
      parseIncompleteMarkdown
      skipHtml
      allowedElements={ALLOWED}
      unwrapDisallowed
      components={md}
      controls={false}
    >
      {text}
    </Streamdown>
  );
}

// --------------------------------------------------------------------- turns

export function Turn({
  role,
  children,
  incomplete = false,
  streaming = false,
}: {
  role: "user" | "assistant";
  children: React.ReactNode;
  incomplete?: boolean;
  streaming?: boolean;
}) {
  const wags = role === "assistant";
  return (
    <div data-testid={wags ? "wags-turn" : "tsims-turn"} className={wags ? "mt-4" : "pt-6"}>
      <p className="label-mono flex items-center gap-2 text-text-muted">
        {wags ? "Wags" : "Tsims"}
        {streaming && (
          <span data-testid="wags-streaming" className="inline-flex items-center gap-1 normal-case tracking-normal">
            <span aria-hidden className="size-1.5 animate-pulse rounded-pill bg-accent" />
            <span className="sr-only">answering</span>
          </span>
        )}
      </p>
      <div className={`mt-1.5 text-base text-pretty ${wags ? "text-text" : "whitespace-pre-wrap text-text-muted"}`}>
        {children}
      </div>
      {incomplete && <p className="label-mono mt-2 text-text-muted">Cut short · what arrived is saved</p>}
    </div>
  );
}

/** One exchange: the question and everything Wags said to it, under a hairline. */
export function Exchange({ children }: { children: React.ReactNode }) {
  return <section className="border-t border-border pb-6 first:border-t-0 first:pt-0">{children}</section>;
}

// ------------------------------------------------------------------ proposals

const KIND: Record<WagsToolName, string> = {
  propose_goal: "Goal",
  propose_venture_log: "Venture log",
  propose_next_action: "Next action",
};

const HORIZON: Record<string, string> = { weekly: "this week", monthly: "this month", long_term: "long term" };

/** What a proposal will change, in one line. */
export function describeProposal(tool: WagsToolName, input: Record<string, unknown>): string {
  const q = (v: unknown) => `“${String(v ?? "").trim()}”`;
  if (tool === "propose_goal") {
    const where = [HORIZON[String(input.horizon)] ?? "a goal", input.area, input.venture_slug].filter(Boolean).join(" · ");
    return `Add ${q(input.title)} · ${where}`;
  }
  if (tool === "propose_venture_log") return `Log a ${String(input.kind ?? "note")} on ${input.venture_slug}: ${q(input.entry)}`;
  const on = input.workstream ? `${input.venture_slug} · ${input.workstream}` : String(input.venture_slug);
  return `Set the next action on ${on} to ${q(input.next_action)}`;
}

export type ProposalState =
  | { kind: "drafting" }
  | { kind: "pending"; busy?: boolean; error?: string | null }
  | { kind: "answered"; outcome: ProposalOutcome };

export function ProposalRow({
  tool,
  input,
  state,
  onConfirm,
  onDismiss,
}: {
  tool: WagsToolName;
  input: Record<string, unknown>;
  state: ProposalState;
  onConfirm?: () => void;
  onDismiss?: () => void;
}) {
  const answered = state.kind === "answered" ? state.outcome : null;
  return (
    <div
      data-testid="wags-proposal"
      data-state={answered ? answered.status : state.kind}
      className="mt-4 border-t border-border pt-3"
    >
      <p className="label-mono text-text-muted">
        Proposal · {KIND[tool]}
        {answered && (
          <span className={answered.status === "confirmed" ? "text-hit" : ""}>
            {" · "}
            {answered.status === "confirmed" ? "Confirmed" : answered.status === "dismissed" ? "Dismissed" : "Not answered"}
          </span>
        )}
      </p>
      <p className={`mt-1 text-sm text-pretty ${answered?.status === "dismissed" ? "text-text-muted line-through" : "text-text"}`}>
        {state.kind === "drafting" ? "Drafting a proposal…" : describeProposal(tool, input)}
      </p>
      {answered?.status === "confirmed" && answered.summary && (
        <p className="mt-1 font-mono text-xs text-text-muted">{answered.summary}</p>
      )}
      {state.kind === "pending" && (
        <>
          <div className="mt-2 flex items-center gap-2">
            <button type="button" onClick={onConfirm} disabled={state.busy} className={button.primary}>
              {state.busy ? "Saving…" : "Confirm"}
            </button>
            <button type="button" onClick={onDismiss} disabled={state.busy} className={button.ghost}>
              Dismiss
            </button>
          </div>
          {state.error && (
            <p role="alert" className="mt-2 text-sm text-danger">
              {state.error}
            </p>
          )}
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------- chrome pieces

/** "Context · Ventures · Sail Beach Club", removable. */
export function ContextChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span data-testid="wags-context-chip" className="inline-flex h-8 max-w-full items-center rounded-pill border border-border pl-3 font-mono text-xs text-text-muted">
      <span className="truncate">Context · {label}</span>
      <button
        type="button"
        onClick={onRemove}
        aria-label="Remove page context"
        className="tap -my-1.5 inline-flex shrink-0 items-center justify-center rounded-pill hover:text-text"
      >
        <X size={14} weight="bold" aria-hidden />
      </button>
    </span>
  );
}

export const STARTERS = [
  { label: "What should I focus on today?", send: true },
  { label: "What's due this week?", send: true },
  { label: "Pressure-test an idea", send: false, prefill: "Pressure-test this idea: " },
  { label: "Summarize my week", send: true },
] as const;

export function Starters({ onPick }: { onPick: (s: (typeof STARTERS)[number]) => void }) {
  return (
    <div data-testid="wags-starters">
      <p className="font-display text-3xl text-text">Ask Wags.</p>
      <p className="mt-2 text-sm text-text-muted">He reads your goals, ventures and capital as they stand right now.</p>
      <ul className="mt-6 border-t border-border">
        {STARTERS.map((s) => (
          <li key={s.label} className="border-b border-border">
            <button
              type="button"
              onClick={() => onPick(s)}
              className="tap flex w-full items-center justify-between gap-4 text-left text-base text-text transition-colors hover:text-accent"
            >
              {s.label}
              <span aria-hidden className="font-mono text-xs text-text-muted">
                {s.send ? "Ask" : "Draft"}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The mono footer: "Sonnet · $0.42 of $20 this month", or budget mode. */
export function StatusLine({
  model,
  budgetMode,
  spentUsd,
  budgetUsd,
}: {
  model: string;
  budgetMode: boolean;
  spentUsd: number;
  budgetUsd: number;
}) {
  const of = `$${spentUsd.toFixed(2)} of $${budgetUsd.toFixed(budgetUsd % 1 ? 2 : 0)} this month`;
  return (
    <p data-testid="wags-status" className="font-mono text-xs text-text-muted tabular-nums">
      {budgetMode ? <span className="text-on-pace">Haiku · budget mode</span> : model} · {of}
    </p>
  );
}

/** A refusal (budget, rate limit, too long, unreachable): a plain sentence. */
export function Refusal({ message }: { message: string }) {
  return (
    <p role="alert" data-testid="wags-refusal" className="mt-4 border-l-2 border-danger pl-3 text-sm text-text">
      {message}
    </p>
  );
}

export function LatestPill({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="tap absolute bottom-3 left-1/2 inline-flex -translate-x-1/2 items-center gap-1 rounded-pill border border-border bg-surface px-3 font-mono text-xs text-text-muted transition-colors hover:text-text"
    >
      <ArrowDown size={14} aria-hidden /> latest
    </button>
  );
}
