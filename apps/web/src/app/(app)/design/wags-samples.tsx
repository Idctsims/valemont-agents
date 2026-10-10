"use client";

import { ContextChip, Exchange, Markdown, ProposalRow, Refusal, StatusLine, Turn } from "@/components/wags/parts";

// /design only: every Wags state from samples. Streaming, a proposal (pending,
// confirmed, dismissed), budget mode, a refusal, the context chip.

const noop = () => {};
const goal = { title: "Sign the SBC permit application", horizon: "weekly", area: "business", venture_slug: "sail-beach-club" };

export function WagsConversationSamples() {
  return (
    <div className="max-w-xl">
      <Exchange>
        <Turn role="user">What should I focus on today?</Turn>
        <Turn role="assistant">
          <Markdown
            text={"**Permits.** Goals: 1 of 10 done, and the SBC permit has carried twice. Ventures · SBC: next action is still *call the city*.\n\n- Call the city before noon\n- Leave Gravity Club alone this week"}
          />
          <ProposalRow tool="propose_goal" input={goal} state={{ kind: "pending" }} onConfirm={noop} onDismiss={noop} />
        </Turn>
      </Exchange>
      <Exchange>
        <Turn role="user">Pressure-test the rooftop idea.</Turn>
        <Turn role="assistant" streaming>
          <Markdown streaming text={"The hole is the lease. Ventures · SBC shows no landlord conversation logged, and a rooftop without a lease is a **mood board**"} />
        </Turn>
      </Exchange>
    </div>
  );
}

export function WagsProposalSamples() {
  return (
    <div className="max-w-xl">
      <p className="text-sm text-text-muted">Pending</p>
      <ProposalRow tool="propose_next_action" input={{ venture_slug: "sail-beach-club", workstream: "Permits", next_action: "File the variance request" }} state={{ kind: "pending" }} onConfirm={noop} onDismiss={noop} />
      <p className="mt-6 text-sm text-text-muted">Saving, then a refusal from the server action</p>
      <ProposalRow tool="propose_venture_log" input={{ venture_slug: "sail-beach-club", kind: "decision", entry: "Rooftop is out until the lease is signed." }} state={{ kind: "pending", busy: false, error: "There is no active venture “sail-beach-club”." }} onConfirm={noop} onDismiss={noop} />
      <p className="mt-6 text-sm text-text-muted">Confirmed</p>
      <ProposalRow tool="propose_goal" input={goal} state={{ kind: "answered", outcome: { status: "confirmed", summary: "Added to this week." } }} />
      <p className="mt-6 text-sm text-text-muted">Dismissed</p>
      <ProposalRow tool="propose_goal" input={goal} state={{ kind: "answered", outcome: { status: "dismissed", summary: "Dismissed." } }} />
    </div>
  );
}

export function WagsChromeSamples() {
  return (
    <div className="flex max-w-xl flex-col gap-4">
      <div>
        <ContextChip label="Ventures · Sail Beach Club" onRemove={noop} />
      </div>
      <StatusLine model="Sonnet" budgetMode={false} spentUsd={0.42} budgetUsd={20} />
      <StatusLine model="Sonnet" budgetMode spentUsd={16.4} budgetUsd={20} />
      <Refusal message="This month's AI budget is spent ($20.00 of $20.00). Wags is back on the 1st, or when the budget is raised." />
      <Refusal message="That's 12 messages in a minute. Give it a moment, then ask again." />
      <Turn role="assistant" incomplete>
        <Markdown text="The SBC permit is the whole week. Everything else" />
      </Turn>
    </div>
  );
}
