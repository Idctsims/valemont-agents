import type { ProposalOutcome, WagsUIMessage } from "./tools";

// The pure half of a thread (no server imports, so e2e/wags-unit.spec.ts runs
// it directly): stored rows to AI SDK UI messages, and the model's copy.
//
// A 'tool' row answers a proposal made in an earlier assistant message; it is
// folded back into that message's tool part, so the UI shows the outcome and
// the model sees a tool_result for its tool_use.

export type MessageRow = {
  id: number;
  role: "user" | "assistant" | "tool";
  content: string;
  parts: unknown[];
  model: string | null;
  incomplete: boolean;
  cost_usd: number | string | null;
  created_at: string;
};

export type ThreadRow = {
  id: string;
  title: string | null;
  origin_page: string | null;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
};

type ToolRowPart = { toolCallId: string; output: ProposalOutcome };

const isToolPart = (p: unknown): p is { type: string; toolCallId: string; state: string; output?: unknown } =>
  !!p && typeof p === "object" && typeof (p as { type?: unknown }).type === "string" &&
  (p as { type: string }).type.startsWith("tool-") && "toolCallId" in p;

export function rowsToMessages(rows: MessageRow[]): WagsUIMessage[] {
  const out: WagsUIMessage[] = [];
  const byCall = new Map<string, { type: string; state: string; output?: unknown }>();
  for (const r of rows) {
    if (r.role === "tool") {
      for (const p of r.parts as ToolRowPart[]) {
        const part = byCall.get(p.toolCallId);
        if (part) Object.assign(part, { state: "output-available", output: p.output });
      }
      continue;
    }
    const parts = structuredClone(r.parts) as WagsUIMessage["parts"];
    for (const p of parts) if (isToolPart(p)) byCall.set(p.toolCallId, p);
    out.push({
      id: `m${r.id}`,
      role: r.role,
      parts,
      metadata:
        r.role === "assistant"
          ? {
              model: r.model ?? undefined,
              incomplete: r.incomplete || undefined,
              costUsd: r.cost_usd == null ? undefined : Number(r.cost_usd),
            }
          : undefined,
    });
  }
  return out;
}

/**
 * The model's copy. Every tool call must carry a result, so a proposal Tsims
 * has not answered yet reads as "not answered"; a tool call cut off mid-input
 * (an interrupted stream) is dropped, and so is an assistant turn left empty.
 */
export function forModel(messages: WagsUIMessage[]): WagsUIMessage[] {
  type Loose = { type: string; text?: string; state?: string; [k: string]: unknown };
  const out: WagsUIMessage[] = [];
  for (const m of messages) {
    const parts = (m.parts as unknown as Loose[]).flatMap((p): Loose[] => {
      if (!isToolPart(p)) return [p];
      if (p.state === "input-streaming") return [];
      if (p.state === "input-available") {
        const output: ProposalOutcome = { status: "not_answered", summary: "Tsims has not answered this proposal yet." };
        return [{ ...p, state: "output-available", output }];
      }
      return [p];
    });
    const meaningful = parts.some((p) => (p.type === "text" ? (p.text ?? "").trim().length > 0 : p.type !== "step-start"));
    if (m.role === "assistant" && !meaningful) continue;
    out.push({ ...m, parts: parts as unknown as WagsUIMessage["parts"] });
  }
  return out;
}
