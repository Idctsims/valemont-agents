import { BriefCard } from "@/components/brief/brief-card";
import { BriefPage } from "@/components/brief/brief-page";
import type { Brief } from "@/lib/brief/types";

// /design only: every Morning Brief state from samples. AI, fallback ("plain
// mode"), late (written after the 9:45 push), and no brief yet.

const AI: Brief = {
  id: 1,
  date: "2026-10-11",
  createdAt: "2026-10-11T11:15:00Z",
  fallback: false,
  model: "claude-sonnet-5-5",
  wordCount: 146,
  isTest: false,
  body: {
    headline: "Permits decide the week; the DEP filing is four days late",
    one_move: "File the DEP application before noon, then call the lender.",
    sections: [
      { key: "goals", title: "Goals", lines: ["Goals: 2 of 8 done, with three days left.", "The SBC permit has carried twice. Third time is a pattern."] },
      { key: "ventures", title: "Ventures", lines: ["Sail Beach Club: DEP filing, overdue since Oct 7.", "Sail Beach Club: lender call due today.", "Gravity Club next: pick a renderer."] },
      { key: "capital", title: "Capital", lines: ["Paper: $1,000.00, flat on the day. Paper, not real money."] },
    ],
  },
};

const FALLBACK: Brief = {
  ...AI,
  id: 2,
  fallback: true,
  model: null,
  body: {
    headline: "Sunday: 1 overdue, 1 due today",
    one_move: "Clear Sail Beach Club: DEP filing (overdue since Oct 7).",
    sections: [
      { key: "goals", title: "Goals", lines: ["Goals: 2 of 8 done this week.", "Carried 2 times: Sign the SBC permit."] },
      { key: "ventures", title: "Ventures", lines: ["Sail Beach Club: DEP filing (overdue since Oct 7).", "Sail Beach Club: Lender call (due today)."] },
      { key: "capital", title: "Capital", lines: ["Paper: $1,000.00 (+$0.00 today)."] },
    ],
  },
};

const LATE: Brief = { ...AI, id: 3, createdAt: "2026-10-11T15:42:00Z" };

export function BriefCardSamples() {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-text-muted">Home, AI brief (closed)</p>
      <BriefCard brief={AI} />
      <p className="text-sm text-text-muted">Home, fallback brief: plain mode (open)</p>
      <BriefCard brief={FALLBACK} startOpen />
      <p className="text-sm text-text-muted">Home, late: written after the 9:45 push</p>
      <BriefCard brief={LATE} />
    </div>
  );
}

export function BriefPageSamples() {
  return (
    <div className="flex flex-col gap-16">
      <div>
        <p className="mb-4 text-sm text-text-muted">/brief, today, with history</p>
        <BriefPage brief={AI} history={[AI, { ...FALLBACK, id: 9, date: "2026-10-10" }]} day="2026-10-11" isToday />
      </div>
      <div>
        <p className="mb-4 text-sm text-text-muted">/brief before 6:15 AM: no brief yet</p>
        <BriefPage brief={null} history={[]} day="2026-10-11" isToday />
      </div>
    </div>
  );
}
