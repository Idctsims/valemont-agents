import type { Metadata } from "next";
import Link from "next/link";

import { BetLegRow, type Leg } from "@/components/ui/bet-leg-row";
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
