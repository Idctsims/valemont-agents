import type { Metadata } from "next";

import { headers } from "next/headers";

import { MoveProposals } from "@/components/feed/move-proposals";
import { StatusChip, type LegStatus } from "@/components/ui/badges";
import { Fold } from "@/components/ui/fold";
import { PageHeader } from "@/components/ui/page-header";
import { requireOwner } from "@/lib/auth";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import {
  boostsNow, feedProfile, moveProposals, techSourcesHealth,
  type BoostNow, type FollowSummary, type ProfileSummary, type TechSourceRow,
} from "@/lib/feed/data";
import { ago, type Pillar } from "@/lib/feed/types";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Feed profile" };

// The learning engine in plain words (db/039 interest_profiles, follows), so
// the owner can SEE learning happen: what each feed has learned to like and
// skip, how much the day-one seeds still count, what is followed by tier,
// and how the profile moved rebuild by rebuild. Reads only, through RLS; the
// worker rebuilds at 04:00 Chicago (feed_profile).

const PILLAR_LABEL: Record<Pillar, string> = { home: "Home", sports: "Sports", tech: "Tech", screening: "Screening" };
const TOP = 6;

function top(weights: Record<string, number>, sign: 1 | -1): [string, number][] {
  return Object.entries(weights)
    .filter(([, w]) => w * sign > 0.005)
    .sort((a, b) => (b[1] - a[1]) * sign)
    .slice(0, TOP);
}

const pretty = (name: string) => name.replaceAll("_", " ");
const pct = (x: number) => `${Math.round(x * 100)}%`;

function WeightList({ title, items, testId }: { title: string; items: [string, number][]; testId: string }) {
  return (
    <div data-testid={testId}>
      <h3 className="label-mono text-text-muted">{title}</h3>
      {items.length === 0 ? (
        <p className="mt-2 text-sm text-text-muted">Nothing yet.</p>
      ) : (
        <ul className="mt-2 flex flex-col gap-1">
          {items.map(([name, w]) => (
            <li key={name} className="flex items-baseline justify-between gap-3 text-sm">
              <span className="min-w-0 truncate text-text">{pretty(name)}</span>
              <span className="shrink-0 font-mono text-xs text-text-muted">
                {w > 0 ? "+" : "−"}
                {Math.abs(w).toFixed(2)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PillarProfile({ p, now }: { p: ProfileSummary; now: number }) {
  const seedLeft = p.seedWeightLove > 0 ? p.seedWeightLove : null;
  return (
    <section data-testid={`profile-${p.pillar}`} className="border-t border-border pt-5 pb-8">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="font-display text-2xl text-text">{PILLAR_LABEL[p.pillar]}</h2>
        <p data-testid="profile-built" className="font-mono text-xs text-text-muted">
          Rebuilt {ago(p.builtAt, now)} · over {p.meanN} stories
        </p>
      </div>

      <dl data-testid="profile-counts" className="mt-4 grid grid-cols-4 gap-2 text-sm">
        {(
          [
            ["Loved", p.loveCount],
            // The Not interested count. "Not interested" wraps in a quarter
            // of 390 px, and to the owner "skip" means no tap (2026-10-12).
            ["Hidden", p.niCount],
            ["Opened", p.openCount],
            ["Neutral", p.neutralCount],
          ] as const
        ).map(([label, n]) => (
          <div key={label}>
            <dt className="label-mono text-text-muted">{label}</dt>
            <dd className="mt-1 font-mono text-lg text-text">{n}</dd>
          </div>
        ))}
      </dl>

      <p data-testid="profile-seed" className="mt-4 text-sm text-text-muted text-pretty">
        {seedLeft === null
          ? "No day-one seeds here: this feed learns from your reactions and follows alone."
          : `Your day-one seeds still make up ${pct(seedLeft)} of what this feed loves${
              p.seedWeightNi > 0 ? ` and ${pct(p.seedWeightNi)} of what it skips` : ""
            }. Each reaction shifts that toward what you actually tap.`}
      </p>

      <div className="mt-6 grid gap-6 sm:grid-cols-2">
        <WeightList title="Topics you like" items={top(p.topics, 1)} testId="profile-topics-up" />
        <WeightList title="Topics you skip" items={top(p.topics, -1)} testId="profile-topics-down" />
        <WeightList title="Sources you like" items={top(p.sources, 1)} testId="profile-sources-up" />
        <WeightList title="Sources you skip" items={top(p.sources, -1)} testId="profile-sources-down" />
        <WeightList title="Names you like" items={top(p.entities, 1)} testId="profile-entities-up" />
        <WeightList title="Names you skip" items={top(p.entities, -1)} testId="profile-entities-down" />
      </div>
    </section>
  );
}

const TIER_LABEL = { 1: "Tier 1 · first", 2: "Tier 2", 3: "Tier 3 · by league" } as const;

function Follows({ follows }: { follows: FollowSummary[] }) {
  return (
    <section data-testid="profile-follows" className="border-t border-border pt-5 pb-8">
      <h2 className="font-display text-2xl text-text">Follows</h2>
      <p className="mt-1 text-sm text-text-muted text-pretty">
        A story naming one of these ranks higher. Alabama is held near the top of For you; at most five tier-1 sports stories reach Home&apos;s first page, and at most six stories per tier-1 name reach Sports&apos; (nine in that name&apos;s game week).
      </p>
      {([1, 2, 3] as const).map((tier) => {
        const rows = follows.filter((f) => f.tier === tier);
        if (rows.length === 0) return null;
        return (
          <div key={tier} className="mt-5">
            <h3 className="label-mono text-text-muted">{TIER_LABEL[tier]}</h3>
            <ul className="mt-2 flex flex-wrap gap-2">
              {rows.map((f) => (
                <li
                  key={`${f.name}-${f.pillar ?? "both"}`}
                  data-testid="follow"
                  className="rounded-pill border border-border px-3 py-1 text-sm text-text"
                >
                  {f.name}
                  {f.floor && <span className="label-mono ml-2 text-accent">Near the top</span>}
                  {f.via && <span className="label-mono ml-2 text-text-muted">{f.via}&apos;s team</span>}
                  {f.pillar && <span className="label-mono ml-2 text-text-muted">{PILLAR_LABEL[f.pillar]}</span>}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </section>
  );
}

const FEED_INTERVAL_MS = 30 * 60 * 1000;

/** /settings/sources' rule: off, failing (6 in a row), N failed, not yet,
 * stale (two missed runs), OK. */
function techStatus(s: TechSourceRow, now: number): { status: LegStatus; label: string } {
  if (!s.active) return { status: "dead", label: "Off" };
  if (s.failures >= 6) return { status: "danger", label: "Failing" };
  if (s.failures > 0) return { status: "on-pace", label: `${s.failures} failed` };
  if (!s.lastOkAt) return { status: "on-pace", label: "Not yet" };
  if (now - Date.parse(s.lastOkAt) > 2 * FEED_INTERVAL_MS) return { status: "danger", label: "Stale" };
  return { status: "hit", label: "OK" };
}

/** Tech Hub's sources (db/045): each one's last good fetch, newest item and
 * failures in a row, so a broken page watcher shows here before its items
 * go missing from /tech. */
function TechSources({ rows, now }: { rows: TechSourceRow[]; now: number }) {
  return (
    <section data-testid="tech-sources" className="border-t border-border pt-5 pb-8">
      <h2 className="font-display text-2xl text-text">Tech sources</h2>
      <p className="mt-1 text-sm text-text-muted text-pretty">
        Checked every 30 minutes. Official sources are summarized first; a failing one alerts after six misses in a row.
      </p>
      {rows.length === 0 ? (
        <p className="mt-3 text-sm text-text-muted">No tech sources yet. They sync at the worker&apos;s next start.</p>
      ) : (
        <ul className="mt-3 flex flex-col">
          {rows.map((s) => (
            <li key={s.slug} data-testid="tech-source" data-slug={s.slug} className="border-b border-border py-3">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                <span className="text-sm text-text">
                  {s.name}
                  {s.official && <span className="label-mono ml-2 text-accent">Official</span>}
                  {!s.active && <span className="label-mono ml-2 text-text-muted">Off</span>}
                </span>
                <span data-testid="tech-source-status">
                  <StatusChip {...techStatus(s, now)} />
                </span>
              </div>
              <p className="mt-1 font-mono text-xs text-text-muted">
                last ok {ago(s.lastOkAt, now)} · last item {ago(s.lastItemAt, now)} · {s.items24h} in 24 h
              </p>
              {(s.failures > 0 && s.lastError) || (!s.active && s.notes) ? (
                <p className="mt-1 text-xs text-text-muted text-pretty">{s.failures > 0 ? s.lastError : s.notes}</p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const CHICAGO_DAY = new Intl.DateTimeFormat("en-US", { timeZone: "America/Chicago", weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

function Boosts({ boosts }: { boosts: BoostNow[] }) {
  return (
    <section data-testid="profile-boosts" className="border-t border-border pt-5 pb-8">
      <h2 className="font-display text-2xl text-text">Event boosts</h2>
      <p className="mt-1 text-sm text-text-muted text-pretty">
        Game weeks and race weekends rank their stories about 12 hours fresher.
      </p>
      {boosts.length === 0 ? (
        <p className="mt-3 text-sm text-text-muted">None active right now.</p>
      ) : (
        <ul className="mt-3 flex flex-col gap-1">
          {boosts.map((b) => (
            <li key={b.label} data-testid="boost" className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
              <span className="text-text">{b.label}</span>
              <span className="font-mono text-xs text-text-muted">until {CHICAGO_DAY.format(new Date(b.endsAt))}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default async function FeedProfilePage() {
  await requireOwner();
  const supabase = await createClient();
  // Display only: the worker builds on the database clock.
  // eslint-disable-next-line react-hooks/purity
  const now = Date.now();
  // Test proposals only for a verified e2e request (the rule for test stories).
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  const [{ latest, history, follows, pendingReactions }, proposals, boosts, techSources] = await Promise.all([
    feedProfile(supabase),
    moveProposals(supabase, { withTests }),
    boostsNow(supabase, new Date(now).toISOString()),
    techSourcesHealth(supabase),
  ]);
  const pillars = (["home", "sports", "tech"] as const).filter((p) => latest[p]);

  return (
    <>
      <PageHeader
        label="Settings"
        title="Feed profile"
        summary="What the feed has learned from your Love, Neutral and Not interested taps. It rebuilds every night at 4:00."
      />
      <p data-testid="profile-pending" className="mb-6 font-mono text-sm text-text-muted">
        {pendingReactions === 0
          ? "No new reactions since the last rebuild."
          : `${pendingReactions} reaction${pendingReactions === 1 ? "" : "s"} since the last rebuild, counted at the next one.`}
      </p>

      <div className="lg:max-w-3xl">
        {pillars.length === 0 ? (
          <p data-testid="profile-empty" className="border-t border-border pt-4 text-base text-text-muted">
            No profile yet. The worker builds the first one at its next start or at 4:00.
          </p>
        ) : (
          pillars.map((p) => <PillarProfile key={p} p={latest[p]!} now={now} />)
        )}

        <MoveProposals proposals={proposals} />

        <Follows follows={follows} />

        <Boosts boosts={boosts} />

        <TechSources rows={techSources} now={now} />

        {history.length > 0 && (
          <Fold label="Rebuilds" count={history.length} testId="profile-history">
            {history.map((h) => (
              <li key={h.id} data-testid="profile-history-row" className="border-b border-border py-3 font-mono text-xs text-text-muted">
                <span className="text-text">{PILLAR_LABEL[h.pillar]}</span> · {ago(h.builtAt, now)} · loved {h.loveCount} · hidden{" "}
                {h.niCount} · opened {h.openCount} · seeds {pct(h.seedWeightLove)}
              </li>
            ))}
          </Fold>
        )}
      </div>
    </>
  );
}
