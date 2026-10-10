import { PROPOSAL_TOOLS, type WagsToolName } from "./tools";

// Proposals in words, shared by the confirm row (client), the route's
// generated line (server) and the unit specs. Pure: no server imports.
//
// SILENT PROPOSALS (Chat 2 Phase 5, Step 0 item 4). The persona requires one
// plain sentence before any propose_* call, but Sonnet 5.5 cannot turn
// thinking fully off, and on 2026-10-10 a turn (message 188) came back as a
// thinking block plus a proposal and no words. proposalLineTransform() closes
// that: when a step ends with a proposal and no text, it inserts a line built
// from the proposal ("Proposing: add goal “X” · this week · Sail Beach Club"),
// marked { wags: { generated: true } }, before the step finishes, so the live
// UI, the stored row and the next turn's history all carry it.

/** slug -> venture name, so a row says "Sail Beach Club", not "sail-beach-club". */
export type VentureNames = Record<string, string>;

const HORIZON: Record<string, string> = { weekly: "this week", monthly: "this month", long_term: "long term" };

const q = (v: unknown) => `“${String(v ?? "").trim()}”`;

/** What a proposal will change, in one line. Venture slugs become names. */
export function describeProposal(tool: WagsToolName, input: Record<string, unknown>, names: VentureNames = {}): string {
  const venture = (slug: unknown) => (slug ? (names[String(slug)] ?? String(slug)) : null);
  if (tool === "propose_goal") {
    const where = [HORIZON[String(input.horizon)] ?? "a goal", input.area, venture(input.venture_slug)].filter(Boolean).join(" · ");
    return `Add goal ${q(input.title)} · ${where}`;
  }
  if (tool === "propose_venture_log") {
    return `Log a ${String(input.kind ?? "note")} on ${venture(input.venture_slug)}: ${q(input.entry)}`;
  }
  const on = input.workstream ? `${venture(input.venture_slug)} · ${input.workstream}` : venture(input.venture_slug);
  return `Set the next action on ${on} to ${q(input.next_action)}`;
}

/** The generated sentence for a proposal that arrived without one. */
export function proposalLine(tool: WagsToolName, input: Record<string, unknown>, names: VentureNames = {}): string {
  const d = describeProposal(tool, input, names);
  return `Proposing: ${d.charAt(0).toLowerCase()}${d.slice(1)}.`;
}

/** The marker on a generated text part. */
export const GENERATED = { wags: { generated: true } } as const;

export function isGenerated(part: object): boolean {
  const m = (part as { providerMetadata?: { wags?: { generated?: unknown } } }).providerMetadata;
  return m?.wags?.generated === true;
}

const isProposal = (name: unknown): name is WagsToolName => PROPOSAL_TOOLS.includes(name as WagsToolName);

type Part = { type: string; [k: string]: unknown };

/**
 * For streamText's experimental_transform. Per step: if a proposal was
 * called and no text was written, emit the generated line just before the
 * step's finish-step, so it belongs to the same assistant message.
 */
export function proposalLineTransform(names: VentureNames = {}) {
  return () => {
    let sawText = false;
    let calls: { tool: WagsToolName; input: Record<string, unknown> }[] = [];
    let n = 0;
    return new TransformStream<Part, Part>({
      transform(part, controller) {
        if (part.type === "start-step") {
          sawText = false;
          calls = [];
        } else if (part.type === "text-delta" && String(part.text ?? "").trim()) {
          sawText = true;
        } else if (part.type === "tool-call" && isProposal(part.toolName)) {
          calls.push({ tool: part.toolName, input: (part.input ?? {}) as Record<string, unknown> });
        } else if (part.type === "finish-step" && calls.length && !sawText) {
          const id = `generated-${++n}`;
          const text = calls.map((c) => proposalLine(c.tool, c.input, names)).join(" ");
          controller.enqueue({ type: "text-start", id, providerMetadata: GENERATED });
          controller.enqueue({ type: "text-delta", id, text, providerMetadata: GENERATED });
          controller.enqueue({ type: "text-end", id, providerMetadata: GENERATED });
          sawText = true;
        }
        controller.enqueue(part);
      },
    });
  };
}
