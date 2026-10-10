import type { Metadata } from "next";
import Link from "next/link";

import { LedgerHeader, MovedOnFold } from "@/components/goals/goal-ledger";
import { GoalRow } from "@/components/goals/goal-row";
import { ProgressStrip } from "@/components/goals/progress-strip";
import { BetLegRow, type Leg } from "@/components/ui/bet-leg-row";
import { countLabel, type Goal, type Segment } from "@/lib/goals/types";
import type { Venture, VentureDate } from "@/lib/ventures/types";
import { VentureRow } from "@/components/ventures/venture-row";

import { LogSamples, TodaySamples, WorkstreamSamples } from "./venture-samples";
import { CapitalComposerSamples, CapitalEntrySamples, CapitalHeroSamples, CapitalTagSamples } from "./capital-samples";
import { PaperBadge, StatusChip } from "@/components/ui/badges";
import { button, chip } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import type { Theme } from "@/lib/theme";

import { TokenValue } from "./token-value";

export const metadata: Metadata = { title: "Design tokens" };

const COLOR_GROUPS: { name: string; tokens: { token: string; swatch: string }[] }[] = [
  {
    name: "Surfaces",
    tokens: [
      { token: "bg", swatch: "bg-bg" },
      { token: "surface", swatch: "bg-surface" },
      { token: "surface-2", swatch: "bg-surface-2" },
      { token: "surface-3", swatch: "bg-surface-3" },
      { token: "border", swatch: "bg-border" },
    ],
  },
  {
    name: "Ink and brass",
    tokens: [
      { token: "text", swatch: "bg-text" },
      { token: "text-muted", swatch: "bg-text-muted" },
      { token: "accent", swatch: "bg-accent" },
      { token: "accent-strong", swatch: "bg-accent-strong" },
      { token: "wood", swatch: "bg-wood" },
    ],
  },
  {
    name: "Status",
    tokens: [
      { token: "hit", swatch: "bg-hit" },
      { token: "on-pace", swatch: "bg-on-pace" },
      { token: "danger", swatch: "bg-danger" },
      { token: "dead", swatch: "bg-dead" },
      { token: "live", swatch: "bg-live" },
    ],
  },
];

const TYPE: { role: string; className: string; sample: string }[] = [
  { role: "Display · Instrument Serif", className: "font-display text-display", sample: "Valemont" },
  { role: "4xl · page title", className: "font-display text-4xl", sample: "Capital Tracker" },
  { role: "3xl · card title", className: "font-display text-3xl", sample: "Tonight's slate" },
  { role: "2xl · Geist", className: "text-2xl font-medium", sample: "Three legs on pace" },
  { role: "lg · lede", className: "text-lg", sample: "The brief reads in under a minute." },
  { role: "base · body", className: "text-base", sample: "Every commitment is written before the outcome is known." },
  { role: "sm · secondary", className: "text-sm text-text-muted", sample: "Updated 6:02 a.m. from the overnight run." },
  { role: "Mono · numbers", className: "font-mono text-2xl tabular-nums", sample: "+1.84R  0.62  −112" },
  { role: "Mono label", className: "label-mono", sample: "Kalshi · NFL · Week 6" },
];

const LEGS: Leg[] = [
  {
    player: "J. Jefferson",
    team: "MIN",
    market: "Receiving yards over",
    line: "74.5",
    current: 47,
    target: 75,
    clock: "Q3 8:12",
    status: "on-pace",
    prob: 0.62,
  },
  {
    player: "Bijan Robinson",
    team: "ATL",
    market: "Rush + rec TDs",
    line: "0.5",
    current: 1,
    target: 1,
    clock: "Q2 0:41",
    status: "hit",
    prob: 1,
  },
  {
    player: "Bills",
    team: "BUF",
    market: "Spread",
    line: "−3.5",
    current: 1,
    target: 4,
    clock: "Q4 2:10",
    status: "danger",
    prob: 0.21,
  },
];

function goal(over: Partial<Goal> & { id: string; title: string }): Goal {
  return {
    notes: null,
    horizon: "weekly",
    area: null,
    period_start: "2026-10-05",
    status: "open",
    carried_from: null,
    carry_count: 0,
    venture_id: null,
    sort_order: 0,
    created_at: "2026-10-05T12:00:00Z",
    completed_at: null,
    moved: false,
    ...over,
  };
}

const GOAL_STATES: { state: string; goal: Goal; overCap?: boolean }[] = [
  { state: "Open", goal: goal({ id: "g1", title: "Send Clipd the revised term sheet", area: "business" }) },
  {
    state: "Done",
    goal: goal({ id: "g2", title: "Four lifts this week", area: "health", status: "done", completed_at: "2026-10-08T12:00:00Z" }),
  },
  {
    state: "Carried",
    goal: goal({ id: "g3", title: "Close out the Q3 books", area: "money", carried_from: "g0", carry_count: 2 }),
  },
  { state: "Dropped", goal: goal({ id: "g4", title: "Reorganise the garage", area: "personal", status: "dropped" }) },
  { state: "Moved on", goal: goal({ id: "g5", title: "Call Marcus about the lease", area: "people", moved: true }) },
  { state: "Over cap", goal: goal({ id: "g6", title: "An eleventh thing", area: "business" }), overCap: true },
];

function venture(over: Partial<Venture> & { id: string; name: string; slug: string }): Venture {
  return {
    tagline: null,
    role: null,
    stage: null,
    next_action: null,
    blockers: null,
    notes: null,
    sort_order: 0,
    archived_at: null,
    ...over,
  };
}

const DESIGN_TODAY = "2026-10-09";
const VENTURE_STATES: { state: string; venture: Venture; dates: VentureDate[] }[] = [
  {
    state: "Set up, with a date",
    venture: venture({
      id: "v1", name: "Sail Beach Club", slug: "sail-beach-club", stage: "pre_launch",
      next_action: "Build 50 LinkedIn connections this week",
    }),
    dates: [{ id: "vd1", venture_id: "v1", workstream_id: null, label: "DEP application", due_on: "2026-10-14", done_at: null }],
  },
  {
    state: "Blocked, date late",
    venture: venture({
      id: "v2", name: "Clipd", slug: "clipd", stage: "building", next_action: "Send revised term sheet",
      blockers: "Waiting on the lead investor's counsel",
    }),
    dates: [{ id: "vd2", venture_id: "v2", workstream_id: null, label: "Term sheet", due_on: "2026-10-07", done_at: null }],
  },
  { state: "Not set up", venture: venture({ id: "v3", name: "Excursion", slug: "excursion" }), dates: [] },
];

const FOLDED: Goal[] = GOAL_STATES.filter((s) => s.state === "Dropped" || s.state === "Moved on").map(
  (s) => s.goal,
);

const STRIP_DEMO: Segment[] = ["done", "done", "done", "open", "open", "open", "empty", "empty", "empty", "empty"];
const STRIP_OVER: Segment[] = [
  "done", "done", "done", "done", "open", "open", "open", "open", "open", "open", "open", "done",
];
const STRIP_HISTORY: Segment[] = ["done", "done", "done", "done", "done", "done", "done", "carried", "carried", "dropped"];

function SubHead({ children }: { children: React.ReactNode }) {
  return <h3 className="label-mono mb-4 text-text-muted">{children}</h3>;
}

function ThemePanel({ theme }: { theme: Theme }) {
  return (
    <section
      data-theme={theme}
      aria-label={`${theme === "night" ? "Night" : "Day"} theme`}
      className="min-w-0 rounded-card border border-border bg-bg p-5 sm:p-8"
    >
      <div className="flex items-baseline justify-between gap-4 border-b rule-wood pb-5">
        <h2 className="font-display text-4xl text-text">{theme === "night" ? "Night" : "Day"}</h2>
        <span className="label-mono text-text-muted">{theme === "night" ? "Default" : "Toggle"}</span>
      </div>

      <div className="mt-8 flex flex-col gap-10">
        <div>
          <SubHead>Colour</SubHead>
          <div className="flex flex-col gap-5">
            {COLOR_GROUPS.map((g) => (
              <div key={g.name}>
                <p className="mb-2 text-sm text-text-muted">{g.name}</p>
                <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5">
                  {g.tokens.map((t) => (
                    <li key={t.token} className="overflow-hidden rounded-inner border border-border bg-surface">
                      <div className={`h-12 ${t.swatch}`} />
                      <div className="flex flex-col gap-0.5 px-3 py-2">
                        <span className="text-sm text-text">{t.token}</span>
                        <TokenValue name={t.token} />
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>

        <div>
          <SubHead>Type</SubHead>
          <ul className="flex flex-col divide-y divide-border">
            {TYPE.map((t) => (
              <li key={t.role} className="flex min-w-0 flex-col gap-1 py-3">
                <span className="label-mono text-text-muted">{t.role}</span>
                <span className={`truncate text-text ${t.className}`}>{t.sample}</span>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <SubHead>Cards</SubHead>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-card border border-border bg-surface p-6">
              <p className="label-mono text-text-muted">Card · 28 radius · surface</p>
              <p className="mt-4 font-display text-3xl text-text">Ventures due today</p>
              <div className="mt-5 rounded-inner bg-surface-2 p-4">
                <p className="label-mono text-text-muted">Inner · 16 radius · surface-2</p>
                <p className="mt-2 text-base text-text">Clipd: send revised term sheet</p>
              </div>
            </div>
            <div className="imagery-sample relative flex min-h-64 items-end overflow-hidden rounded-card p-3">
              <span className="label-mono absolute top-4 left-5 text-text">Imagery stand-in</span>
              <div className="glass w-full rounded-inner p-4">
                <p className="label-mono text-text-muted">Glass · hero over imagery only</p>
                <p className="mt-2 font-display text-2xl text-text">Style DNA</p>
              </div>
            </div>
          </div>
        </div>

        <div>
          <SubHead>Pills and chips</SubHead>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={button.primary}>Generate parlays</button>
            <button type="button" className={button.strong}>I&apos;m playing this</button>
            <button type="button" className={button.secondary}>Edit slip</button>
            <button type="button" className={button.ghost}>Cancel</button>
            <button type="button" className={button.primary} disabled>Saving</button>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <span className={chip(true)}>NFL</span>
            <span className={chip(false)}>NBA</span>
            <span className={chip(false)}>CFB</span>
            <span className={chip(false)}>F1</span>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <StatusChip status="hit" />
            <StatusChip status="on-pace" />
            <StatusChip status="danger" />
            <StatusChip status="dead" />
            <StatusChip status="live" />
            <PaperBadge />
          </div>
        </div>

        <div>
          <SubHead>Bet legs</SubHead>
          <div className="rounded-card border border-border bg-surface px-5 sm:px-6">
            <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-border py-4">
              <div className="flex items-center gap-2">
                <PaperBadge />
                <span className="label-mono whitespace-nowrap text-text-muted">3-leg · Safer</span>
              </div>
              <span className="font-mono text-sm whitespace-nowrap tabular-nums text-text">$10 pays $64.20</span>
            </div>
            <div className="@container divide-y divide-border">
              {LEGS.map((leg) => (
                <BetLegRow key={leg.player} leg={leg} />
              ))}
            </div>
          </div>
        </div>

        <div>
          <SubHead>Goal rows</SubHead>
          <ul className="border-t border-border" data-testid="design-goal-rows">
            {GOAL_STATES.map(({ state, goal: g, overCap }) => (
              <GoalRow key={state} goal={g} overCap={overCap} />
            ))}
          </ul>
          <p className="mt-3 text-sm text-text-muted">
            Open, done, carried twice, dropped, moved on to next week, and an eleventh goal past the soft cap.
          </p>
        </div>

        <div>
          <SubHead>Ledger headers</SubHead>
          <div className="flex flex-col gap-6">
            <div>
              <p className="mb-2 text-sm text-text-muted">Label: Home, where the date is the hero</p>
              <LedgerHeader variant="label" heading="This week" periodLabel="Oct 5 – 11" count={countLabel(2, 6)} />
            </div>
            <div>
              <p className="mb-2 text-sm text-text-muted">Hero: /goals, under the tabs</p>
              <LedgerHeader variant="hero" heading="This week" periodLabel="Oct 5 – 11" count={countLabel(2, 6)} />
            </div>
            <div>
              <p className="mb-2 text-sm text-text-muted">Saving: a tap not yet on the server</p>
              <LedgerHeader variant="label" heading="This week" periodLabel="Oct 5 – 11" count={countLabel(3, 6)} saving />
            </div>
          </div>
        </div>

        <div>
          <SubHead>Moved on fold</SubHead>
          <p className="mb-2 text-sm text-text-muted">Dropped and moved-on goals, after open and done. Closed:</p>
          <MovedOnFold count={2}>
            {FOLDED.map((g) => (
              <GoalRow key={g.id} goal={g} />
            ))}
          </MovedOnFold>
          <p className="mt-4 mb-2 text-sm text-text-muted">Open:</p>
          <MovedOnFold count={2} defaultOpen>
            {FOLDED.map((g) => (
              <GoalRow key={g.id} goal={g} />
            ))}
          </MovedOnFold>
        </div>

        <div>
          <SubHead>Venture rows</SubHead>
          <ul className="border-t border-border" data-testid="design-venture-rows">
            {VENTURE_STATES.map(({ state, venture: v, dates }) => (
              <VentureRow key={state} venture={v} openDates={dates} today={DESIGN_TODAY} />
            ))}
          </ul>
          <p className="mt-3 text-sm text-text-muted">
            Set up with its nearest date, blocked with a late date, and a name-only venture.
          </p>
        </div>

        <div>
          <SubHead>Workstreams</SubHead>
          <WorkstreamSamples />
          <p className="mt-3 text-sm text-text-muted">Tap a row for its next action, notes and dates. Parked ones fold.</p>
        </div>

        <div>
          <SubHead>Venture log</SubHead>
          <LogSamples />
          <p className="mt-3 text-sm text-text-muted">Auto entries (written by the database) are muted; decisions and milestones are marked.</p>
        </div>

        <div>
          <SubHead>Today</SubHead>
          <TodaySamples />
        </div>

        <div>
          <SubHead>Capital · hero, change, history, breakdown</SubHead>
          <CapitalHeroSamples />
        </div>

        <div>
          <SubHead>Capital · mode tags</SubHead>
          <CapitalTagSamples />
          <p className="mt-3 text-sm text-text-muted">
            Beside every money total. Paper in the on-pace (warning) token, live in the live token; never small grey text.
          </p>
        </div>

        <div>
          <SubHead>Capital · entries</SubHead>
          <CapitalEntrySamples />
          <p className="mt-3 text-sm text-text-muted">
            Newest first, never editable. The ⋯ menu offers Correct with adjustment. The mode shows on each row only once live rows exist.
          </p>
        </div>

        <div>
          <SubHead>Capital · composer</SubHead>
          <CapitalComposerSamples />
        </div>

        <div>
          <SubHead>Week strip</SubHead>
          <div className="flex flex-col gap-4">
            <div>
              <p className="mb-2 text-sm text-text-muted">Live week, 3 of 10 done</p>
              <ProgressStrip segments={STRIP_DEMO} />
            </div>
            <div>
              <p className="mb-2 text-sm text-text-muted">Over the cap: twelve goals</p>
              <ProgressStrip segments={STRIP_OVER} />
            </div>
            <div>
              <p className="mb-2 text-sm text-text-muted">History: done, carried, dropped</p>
              <ProgressStrip segments={STRIP_HISTORY} size="sm" />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

export default function DesignPage() {
  return (
    <>
      <PageHeader
        label="Design tokens"
        title="House style"
        summary="Every colour, size and shape below comes from src/styles/tokens.css, shown in both themes. The values printed on the swatches are read from the live CSS."
      />

      <div className="grid gap-6 xl:grid-cols-2">
        <ThemePanel theme="night" />
        <ThemePanel theme="day" />
      </div>

      <section className="mt-6 grid gap-6 rounded-card border border-border bg-surface p-6 lg:grid-cols-3 lg:p-8">
        <div>
          <SubHead>Shape</SubHead>
          <div className="flex items-end gap-3">
            <div className="size-16 rounded-card border border-border bg-surface-2" />
            <div className="size-12 rounded-inner border border-border bg-surface-2" />
            <div className="h-8 w-16 rounded-pill border border-border bg-surface-2" />
          </div>
          <p className="mt-3 text-sm text-text-muted">Card 28, inner 16, pill. No drop shadows: depth is surface steps and 1px borders.</p>
        </div>
        <div>
          <SubHead>Spacing · 4-pt</SubHead>
          <ul className="flex flex-col gap-1.5">
            {[
              ["1", "w-1"],
              ["2", "w-2"],
              ["4", "w-4"],
              ["6", "w-6"],
              ["8", "w-8"],
              ["12", "w-12"],
              ["16", "w-16"],
            ].map(([n, w]) => (
              <li key={n} className="flex items-center gap-3">
                <span className="label-mono w-8 text-text-muted">{n}</span>
                <span className={`h-2 rounded-pill bg-accent ${w}`} />
              </li>
            ))}
          </ul>
        </div>
        <div>
          <SubHead>Motion</SubHead>
          <p className="text-sm text-text">
            <span className="font-mono">180ms</span> ease-out for controls,{" "}
            <span className="font-mono">320ms</span> for cards and pages. Reduced-motion settings
            switch both off.
          </p>
          <p className="mt-4 text-sm text-text-muted">
            The pixel signature face appears only on the installed app&apos;s cold-start splash screen and the{" "}
            <Link href="/nowhere" className="text-accent underline underline-offset-4">
              404 page
            </Link>
            .
          </p>
        </div>
      </section>
    </>
  );
}
