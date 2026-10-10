import { tool, type InferUITools, type UIMessage } from "ai";
import { z } from "zod";

// Wags proposes; Tsims confirms. These tools have no execute(): the model's
// call streams to the client, which renders a confirm row. Confirm runs the
// EXISTING server actions (goals, ventures), so every rule they enforce still
// applies; the outcome is then written to the thread as a 'tool' row
// (src/app/(app)/wags/actions.ts recordProposalOutcome), which is how Wags
// learns it happened. Shared by the route and the client.

const outcome = z.object({
  status: z.enum(["confirmed", "dismissed", "not_answered"]),
  summary: z.string(),
});

const slug = z
  .string()
  .regex(/^[a-z0-9]+(-[a-z0-9]+)*$/)
  .max(80)
  .describe("The venture's slug, exactly as in the CONTEXT block.");

export const wagsTools = {
  propose_goal: tool({
    description:
      "Propose a new goal for Tsims to confirm. Weekly goals are this week's; monthly this month's. Link a venture only for a business goal.",
    inputSchema: z.object({
      title: z.string().min(1).max(200).describe("An outcome, under 100 characters."),
      horizon: z.enum(["weekly", "monthly", "long_term"]),
      area: z.enum(["business", "personal", "health", "money", "people"]).nullable(),
      venture_slug: slug.nullable().optional(),
    }),
    outputSchema: outcome,
  }),
  propose_venture_log: tool({
    description: "Propose an entry for a venture's running log (a note, a decision or a milestone).",
    inputSchema: z.object({
      venture_slug: slug,
      entry: z.string().min(1).max(4000),
      kind: z.enum(["note", "decision", "milestone"]),
    }),
    outputSchema: outcome,
  }),
  propose_next_action: tool({
    description:
      "Propose a new next action for a venture, or for one of its workstreams when `workstream` names one from the CONTEXT block.",
    inputSchema: z.object({
      venture_slug: slug,
      workstream: z.string().min(1).max(120).nullable().optional(),
      next_action: z.string().min(1).max(300),
    }),
    outputSchema: outcome,
  }),
};

export type WagsToolName = keyof typeof wagsTools;
export const PROPOSAL_TOOLS = Object.keys(wagsTools) as WagsToolName[];

/** What a proposal's confirm row writes back, and what Wags then reads. */
export type ProposalOutcome = z.infer<typeof outcome>;

export type WagsMetadata = {
  /** The model that served this answer; "mock" in local e2e. */
  model?: string;
  /** True when the budget moved Wags to Haiku. */
  budgetMode?: boolean;
  incomplete?: boolean;
  costUsd?: number;
};

export type WagsUIMessage = UIMessage<WagsMetadata, never, InferUITools<typeof wagsTools>>;
