"use client";

import { useChat } from "@ai-sdk/react";
import { Stop, PaperPlaneRight } from "@phosphor-icons/react";
import { DefaultChatTransport, isToolUIPart, getToolName } from "ai";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { StickToBottom, useStickToBottomContext } from "use-stick-to-bottom";

import { createGoal } from "@/app/(app)/goals/actions";
import { addLogEntry, updateVenture, updateWorkstream } from "@/app/(app)/ventures/actions";
import { getWagsStatus, recordProposalOutcome, resolveVenture, type WagsStatus } from "@/app/(app)/wags/actions";
import type { PageContext } from "@/lib/wags/page-context";
import type { ProposalOutcome, WagsToolName, WagsUIMessage } from "@/lib/wags/tools";

import {
  ContextChip,
  Exchange,
  LatestPill,
  Markdown,
  ProposalRow,
  Refusal,
  STARTERS,
  Starters,
  StatusLine,
  Turn,
  type ProposalState,
} from "./parts";

// One Wags thread: the conversation, the proposals, the composer and the
// footer line. Used by the sheet (phone), the panel (desktop) and /wags.
// The server owns the thread: this component sends only the new message,
// the thread id and the page context (src/app/api/wags/route.ts).

const MAX_INPUT = 4000;

/** A refusal from the route is already a sentence; a network failure is not. */
function readable(error: Error): string {
  const m = error.message?.trim();
  if (!m || /failed to fetch|network|load failed/i.test(m)) return "Wags couldn't be reached. Check the connection and try again.";
  return m.length > 300 ? `${m.slice(0, 297)}…` : m;
}

export function Conversation({
  threadId,
  initialMessages,
  page,
  onRemovePage,
  onAnswered,
  autoFocus = false,
}: {
  threadId: string;
  initialMessages: WagsUIMessage[];
  page: PageContext | null;
  onRemovePage?: () => void;
  /** After each finished answer (the thread may have a new title). */
  onAnswered?: () => void;
  autoFocus?: boolean;
}) {
  const pageRef = useRef(page);
  pageRef.current = page;
  const [draft, setDraft] = useState("");
  const [refusal, setRefusal] = useState<string | null>(null);
  const [status, setStatus] = useState<WagsStatus | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  const loadStatus = useCallback(() => {
    getWagsStatus()
      .then((r) => r.ok && setStatus(r.status))
      .catch(() => undefined);
  }, []);
  useEffect(loadStatus, [loadStatus]);

  const transport = useMemo(
    () =>
      new DefaultChatTransport<WagsUIMessage>({
        api: "/api/wags",
        prepareSendMessagesRequest: ({ id, messages }) => ({
          body: { threadId: id, message: messages[messages.length - 1], page: pageRef.current },
        }),
      }),
    [],
  );

  const chat = useChat<WagsUIMessage>({
    id: threadId,
    messages: initialMessages,
    transport,
    onFinish: () => {
      loadStatus();
      onAnswered?.();
    },
    onError: (e) => {
      setRefusal(readable(e));
      // A refused message never reached the thread: take it back off the
      // screen and put its words back in the composer.
      chat.setMessages((ms) => {
        const last = ms[ms.length - 1];
        if (last?.role !== "user") return ms;
        const text = last.parts.map((p) => (p.type === "text" ? p.text : "")).join("");
        setDraft((d) => d || text);
        return ms.slice(0, -1);
      });
    },
  });
  const busy = chat.status === "submitted" || chat.status === "streaming";

  function send(text: string) {
    const clean = text.trim();
    if (!clean || busy) return;
    if (clean.length > MAX_INPUT) {
      setRefusal(`That's ${clean.length.toLocaleString("en-US")} characters. Keep it under ${MAX_INPUT.toLocaleString("en-US")}.`);
      return;
    }
    setRefusal(null);
    chat.clearError();
    setDraft("");
    void chat.sendMessage({ text: clean });
  }

  // ------------------------------------------------------------- proposals
  const [proposals, setProposals] = useState<Record<string, { busy?: boolean; error?: string | null }>>({});

  async function answer(tool: WagsToolName, toolCallId: string, input: Record<string, unknown>, confirm: boolean) {
    setProposals((p) => ({ ...p, [toolCallId]: { busy: true, error: null } }));
    let outcome: ProposalOutcome;
    try {
      outcome = confirm ? await apply(tool, input) : { status: "dismissed", summary: "Dismissed." };
    } catch (e) {
      setProposals((p) => ({ ...p, [toolCallId]: { busy: false, error: e instanceof Error ? e.message : String(e) } }));
      return;
    }
    const saved = await recordProposalOutcome(threadId, toolCallId, tool, outcome).catch(() => null);
    if (!saved?.ok) {
      const error = saved?.error ?? "Couldn't record that. Check the connection and try again.";
      // A confirmed change already happened; say so rather than inviting a duplicate.
      setProposals((p) => ({
        ...p,
        [toolCallId]: { busy: false, error: confirm ? `Done, but not noted in the thread: ${error}` : error },
      }));
      if (!confirm) return;
    } else {
      setProposals((p) => ({ ...p, [toolCallId]: {} }));
    }
    void chat.addToolOutput({ tool, toolCallId, output: outcome } as Parameters<typeof chat.addToolOutput>[0]);
  }

  const exchanges = groupExchanges(chat.messages);
  const last = chat.messages[chat.messages.length - 1];
  const waiting = chat.status === "submitted" && last?.role === "user";
  const lastMeta = [...chat.messages].reverse().find((m) => m.role === "assistant")?.metadata;
  const shown = status && {
    ...status,
    budgetMode: status.budgetMode || !!lastMeta?.budgetMode,
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <StickToBottom className="relative min-h-0 flex-1 overflow-y-auto" resize="smooth" initial="instant">
        <StickToBottom.Content scrollClassName="scroll-gutter-end" className="px-safe pt-4 pb-6 lg:px-6">
          {chat.messages.length === 0 && !busy ? (
            <Starters
              onPick={(s) => {
                if (s.send) send(s.label);
                else {
                  setDraft("prefill" in s ? s.prefill : "");
                  input.current?.focus();
                }
              }}
            />
          ) : (
            exchanges.map((ex, i) => (
              <Exchange key={ex[0]?.id ?? i}>
                {ex.map((m) => (
                  <Message
                    key={m.id}
                    message={m}
                    streaming={chat.status === "streaming" && m === last && m.role === "assistant"}
                    proposalState={(id, answered) => proposalStateOf(id, answered, proposals)}
                    onAnswer={answer}
                  />
                ))}
                {waiting && i === exchanges.length - 1 && (
                  <Turn role="assistant" streaming>
                    <span className="text-text-muted">Reading your pillars…</span>
                  </Turn>
                )}
              </Exchange>
            ))
          )}
          {refusal && <Refusal message={refusal} />}
        </StickToBottom.Content>
        <ScrollToLatest />
      </StickToBottom>

      <Composer
        ref={input}
        value={draft}
        onChange={setDraft}
        onSend={() => send(draft)}
        onStop={() => chat.stop()}
        busy={busy}
        autoFocus={autoFocus}
        chip={page && onRemovePage ? <ContextChip label={page.label} onRemove={onRemovePage} /> : null}
        footer={shown ? <StatusLine {...shown} /> : <p className="font-mono text-xs text-text-muted">…</p>}
      />
    </div>
  );
}

function ScrollToLatest() {
  const { isAtBottom, scrollToBottom } = useStickToBottomContext();
  if (isAtBottom) return null;
  return <LatestPill onClick={() => void scrollToBottom()} />;
}

function groupExchanges(messages: WagsUIMessage[]): WagsUIMessage[][] {
  const out: WagsUIMessage[][] = [];
  for (const m of messages) {
    if (m.role === "user" || out.length === 0) out.push([m]);
    else out[out.length - 1].push(m);
  }
  return out;
}

function proposalStateOf(
  id: string,
  answered: ProposalOutcome | null,
  local: Record<string, { busy?: boolean; error?: string | null }>,
): ProposalState {
  if (answered && answered.status !== "not_answered") return { kind: "answered", outcome: answered };
  return { kind: "pending", busy: local[id]?.busy, error: local[id]?.error };
}

function Message({
  message,
  streaming,
  proposalState,
  onAnswer,
}: {
  message: WagsUIMessage;
  streaming: boolean;
  proposalState: (toolCallId: string, answered: ProposalOutcome | null) => ProposalState;
  onAnswer: (tool: WagsToolName, toolCallId: string, input: Record<string, unknown>, confirm: boolean) => void;
}) {
  if (message.role === "user") {
    const text = message.parts.map((p) => (p.type === "text" ? p.text : "")).join("");
    return <Turn role="user">{text}</Turn>;
  }
  const text = message.parts.map((p) => (p.type === "text" ? p.text : "")).join("");
  const tools = message.parts.filter(isToolUIPart);
  return (
    <Turn role="assistant" streaming={streaming} incomplete={!!message.metadata?.incomplete}>
      {text && <Markdown text={text} streaming={streaming} />}
      {tools.map((p) => {
        const tool = getToolName(p) as WagsToolName;
        const input = (p.input ?? {}) as Record<string, unknown>;
        const state: ProposalState =
          p.state === "input-streaming"
            ? { kind: "drafting" }
            : proposalState(p.toolCallId, p.state === "output-available" ? (p.output as ProposalOutcome) : null);
        return (
          <ProposalRow
            key={p.toolCallId}
            tool={tool}
            input={input}
            state={state}
            onConfirm={() => onAnswer(tool, p.toolCallId, input, true)}
            onDismiss={() => onAnswer(tool, p.toolCallId, input, false)}
          />
        );
      })}
    </Turn>
  );
}

/** Run a confirmed proposal through the EXISTING server actions. */
async function apply(tool: WagsToolName, input: Record<string, unknown>): Promise<ProposalOutcome> {
  const fail = (r: { ok: boolean; error?: string }) => {
    if (!r.ok) throw new Error(r.error);
  };
  if (tool === "propose_goal") {
    const area = (input.area as string | null) ?? null;
    let ventureId: string | null = null;
    if (input.venture_slug && area === "business") {
      const v = await resolveVenture(String(input.venture_slug));
      fail(v);
      if (v.ok) ventureId = v.ventureId;
    }
    const r = await createGoal({
      title: String(input.title ?? ""),
      horizon: input.horizon as "weekly" | "monthly" | "long_term",
      area: area as Parameters<typeof createGoal>[0]["area"],
      ventureId,
    });
    fail(r);
    return { status: "confirmed", summary: `Added to ${input.horizon === "weekly" ? "this week" : input.horizon === "monthly" ? "this month" : "long term"}.` };
  }
  const v = await resolveVenture(String(input.venture_slug), (input.workstream as string | null) ?? null);
  fail(v);
  if (!v.ok) throw new Error(v.error);
  if (tool === "propose_venture_log") {
    fail(await addLogEntry(v.ventureId, input.kind as "note" | "decision" | "milestone", String(input.entry ?? "")));
    return { status: "confirmed", summary: `Logged on ${v.ventureName}.` };
  }
  const next = String(input.next_action ?? "");
  fail(v.workstreamId ? await updateWorkstream(v.workstreamId, { next_action: next }) : await updateVenture(v.ventureId, { next_action: next }));
  return { status: "confirmed", summary: `${v.ventureName}${input.workstream ? ` · ${input.workstream}` : ""} next action set.` };
}

// ------------------------------------------------------------------ composer

function Composer({
  ref,
  value,
  onChange,
  onSend,
  onStop,
  busy,
  autoFocus,
  chip,
  footer,
}: {
  ref: React.RefObject<HTMLTextAreaElement | null>;
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  onStop: () => void;
  busy: boolean;
  autoFocus: boolean;
  chip: React.ReactNode;
  footer: React.ReactNode;
}) {
  // Grow with the text up to max-h-composer, then scroll.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value, ref]);

  useEffect(() => {
    if (autoFocus) ref.current?.focus({ preventScroll: true });
  }, [autoFocus, ref]);

  const near = value.length > MAX_INPUT - 500;
  return (
    <form
      data-testid="wags-composer"
      className="border-t border-border bg-surface px-safe pt-3 pb-3 lg:px-6"
      onSubmit={(e) => {
        e.preventDefault();
        onSend();
      }}
    >
      {chip && <div className="mb-2 flex">{chip}</div>}
      <div className="flex items-end gap-2">
        <label className="sr-only" htmlFor="wags-input">
          Message Wags
        </label>
        <textarea
          id="wags-input"
          ref={ref}
          rows={1}
          value={value}
          maxLength={MAX_INPUT}
          enterKeyHint="send"
          placeholder="Ask Wags…"
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              onSend();
            }
          }}
          className="max-h-composer min-h-11 flex-1 resize-none overflow-y-auto rounded-inner border border-border bg-bg px-3 py-2.5 text-base text-text placeholder:text-text-muted focus:border-accent focus:outline-none"
        />
        {busy ? (
          <button
            type="button"
            onClick={onStop}
            aria-label="Stop"
            className="tap inline-flex shrink-0 items-center justify-center rounded-pill border border-border text-text transition-colors hover:bg-surface-2"
          >
            <Stop size={18} weight="fill" aria-hidden />
          </button>
        ) : (
          <button
            type="submit"
            aria-label="Send"
            disabled={!value.trim()}
            className="tap inline-flex shrink-0 items-center justify-center rounded-pill bg-accent text-on-accent transition-colors hover:bg-accent/90 disabled:border disabled:border-border disabled:bg-transparent disabled:text-text-muted"
          >
            <PaperPlaneRight size={18} weight="fill" aria-hidden />
          </button>
        )}
      </div>
      <div className="mt-2 flex items-center justify-between gap-3">
        {footer}
        {near && (
          <span className="font-mono text-xs text-text-muted tabular-nums">
            {value.length.toLocaleString("en-US")} / {MAX_INPUT.toLocaleString("en-US")}
          </span>
        )}
      </div>
    </form>
  );
}

export { STARTERS };
