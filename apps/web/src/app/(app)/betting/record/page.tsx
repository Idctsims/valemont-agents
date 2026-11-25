import type { Metadata } from "next";
import { headers } from "next/headers";
import Link from "next/link";

import { requireOwner } from "@/lib/auth";
import { loadParlayRecord } from "@/lib/betting/parlay-data";
import { TIER_LEGS, TIER_NAME, cohortName, perDollar, signedUsd, usd, type RecordRow } from "@/lib/betting/parlays";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Betting · Record" };

// Record (Chat 8 Phase 4.5; docs/parlays_page.md §3): each parlay agent on its
// own, one cohort (rule version) at a time, never a figure across tiers or
// cohorts (CLAUDE.md §9.2). The headline is the return per $1 staked (the §9
// R-multiple: a ticket's stake is its whole risk), with its standard error
// clustered by slate day; the paper dollars beside it; CLV (proxy) with its
// close ages, labelled not profit; hit rate small, never the headline.

const AGENTS: { tier: string; scope: string; name: string; sub: string }[] = [
  { tier: "safer", scope: "sport", name: "Safer", sub: TIER_LEGS.safer },
  { tier: "mid", scope: "sport", name: "Mid", sub: TIER_LEGS.mid },
  { tier: "lotto", scope: "sport", name: "Lotto", sub: TIER_LEGS.lotto },
  { tier: "lotto", scope: "cross_sport", name: "Lotto", sub: "cross-sport" },
];

function pct(x: number | undefined): string {
  return typeof x === "number" ? `${x < 0 ? "−" : "+"}${Math.abs(x * 100).toFixed(1)}%` : "—";
}

function Line({ k, v, testId }: { k: React.ReactNode; v: React.ReactNode; testId?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-border py-2.5 last:border-b-0">
      <dt className="text-sm text-text-muted">{k}</dt>
      <dd data-testid={testId} className="text-right font-mono text-sm tabular-nums text-text">{v}</dd>
    </div>
  );
}

function AgentCard({ a, row, sports, cohort }: { a: (typeof AGENTS)[number]; row?: RecordRow; sports: RecordRow[]; cohort: string }) {
  const settled = row?.settled ?? 0;
  const empty = row?.empty ? Object.entries(row.empty).sort((x, y) => y[1] - x[1]) : [];
  return (
    <article data-testid="record-agent" data-tier={a.tier} data-scope={a.scope}
             className="rounded-card border border-border bg-surface p-5">
      <header className="flex flex-wrap items-center gap-2">
        <h2 className="font-semibold text-text">{a.name}</h2>
        <span className="text-sm text-text-muted">{a.sub}</span>
        <span className="label-mono rounded-tile bg-paper-bg px-1.5 text-paper-fg">PAPER</span>
        {a.scope === "cross_sport" && <span className="label-mono rounded-tile border border-on-pace px-1.5 text-on-pace">extrapolated fill</span>}
      </header>
      {!row ? (
        <p className="mt-3 text-text-muted">No ticket yet under {cohortName(cohort).split(" · ")[1] ?? cohort}. Its record starts with its first ticket, apart from every other cohort.</p>
      ) : settled === 0 ? (
        <p className="mt-3 text-text">{row.tickets} ticket{row.tickets === 1 ? "" : "s"}, none settled yet.</p>
      ) : (
        <>
          <p className="mt-2 flex flex-wrap items-baseline gap-x-3">
            <span data-testid="record-r" className="font-display text-4xl text-text">{perDollar(row.r_mean ?? 0).split(" per")[0]}</span>
            <span className="text-sm text-text-muted">per $1 staked{row.r_se != null && row.r_se > 0 ? ` · ± ${row.r_se.toFixed(2)}` : ""}
              {row.days ? ` (${row.days} slate day${row.days === 1 ? "" : "s"})` : ""}</span>
          </p>
          <dl className="mt-2">
            <Line k="Paper result" testId="record-pl" v={`${signedUsd(row.pl ?? 0)} on ${usd(row.staked ?? 0)}`} />
            <Line k="Settled · hit · open" v={`${settled} · ${row.hit}${row.partial ? ` (+${row.partial} partial)` : ""} · ${row.open}`} />
            <Line k={<span>CLV (proxy)<span className="label-mono mt-0.5 block whitespace-nowrap">not profit</span></span>}
                  testId="record-clv"
                  v={row.clv_n
                    ? <span>{pct(row.clv_pct_mean)}<span className="mt-0.5 block whitespace-nowrap text-xs text-text-muted">
                        close ages {row.age_med ?? "—"} / {row.age_max ?? "—"} min</span></span>
                    : "no close read yet"} />
            <Line k="Closes missed" v={row.missed} />
          </dl>
        </>
      )}
      {empty.length > 0 && (
        <p className="mt-3 text-sm text-text-muted"><b className="font-semibold text-text">Empty slots:</b>{" "}
          {empty.map(([r, n]) => `${r} ${n}`).join(" · ")}</p>
      )}
      {sports.length > 0 && (
        <details className="mt-2">
          <summary className="tap flex items-center text-sm font-semibold text-accent-ink">By sport</summary>
          <table className="mt-1 w-full text-sm">
            <thead><tr className="text-left text-xs text-text-muted"><th className="py-1 font-normal">Sport</th>
              <th className="py-1 text-right font-normal">Settled</th><th className="py-1 text-right font-normal">Per $1</th>
              <th className="py-1 text-right font-normal">Paper</th></tr></thead>
            <tbody>
              {sports.map((s) => (
                <tr key={s.sport} className="border-t border-border">
                  <td className="py-1.5 text-text">{s.sport}</td>
                  <td className="py-1.5 text-right font-mono text-text">{s.settled}</td>
                  <td className="py-1.5 text-right font-mono text-text">{s.settled ? perDollar(s.r_mean ?? 0).split(" per")[0] : "—"}</td>
                  <td className="py-1.5 text-right font-mono text-text">{s.settled ? signedUsd(s.pl ?? 0) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </article>
  );
}

export default async function RecordPage({ searchParams }: { searchParams: Promise<{ cohort?: string }> }) {
  await requireOwner();
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  let rec;
  try {
    rec = await loadParlayRecord(await createClient(), withTests);
  } catch (e) {
    console.error(`record: ${(e as Error).message}`);
    return <p role="alert" className="max-w-measure border-t border-border pt-4 text-lg text-text">The record didn&apos;t load.</p>;
  }
  const asked = (await searchParams).cohort;
  const cohort = asked && rec.cohorts.includes(asked) ? asked : "P2";
  return (
    <section aria-labelledby="record-h" data-testid="record-page" data-cohort={cohort}>
      <p className="label-mono text-text-muted">Each tier on its own · never added together</p>
      <h1 id="record-h" className="mt-3 font-display text-4xl text-text lg:text-display">Record</h1>
      <nav aria-label="Cohort" className="mt-5 flex gap-2 overflow-x-auto">
        {rec.cohorts.map((c) => (
          <Link key={c} href={`/betting/record?cohort=${encodeURIComponent(c)}`} aria-current={c === cohort ? "page" : undefined}
                data-testid={`record-cohort-${c}`}
                className={`tap inline-flex items-center rounded-pill border px-4 text-sm ${
                  c === cohort ? "border-text bg-text text-bg" : "border-border text-text-muted hover:text-text"}`}>
            {cohortName(c)}
          </Link>
        ))}
      </nav>
      <div className="mt-5 grid gap-3 lg:grid-cols-2">
        {AGENTS.map((a) => {
          const mine = rec.rows.filter((r) => r.tier === a.tier && r.scope === a.scope && r.rv === cohort);
          return <AgentCard key={`${a.tier}-${a.scope}`} a={a} cohort={cohort}
                            row={mine.find((r) => !r.sport)} sports={mine.filter((r) => r.sport)} />;
        })}
      </div>
      <p className="mt-4 max-w-measure text-sm text-text-muted text-pretty">
        Paper tickets from the Parlay Generator. &ldquo;Per $1 staked&rdquo; is the return on the paper stake, fees in;
        its spread is clustered by slate day. CLV (proxy) is not profit, and it is shown with its close ages.
        {" "}{TIER_NAME.safer}, {TIER_NAME.mid} and {TIER_NAME.lotto} are separate records.
      </p>
    </section>
  );
}
