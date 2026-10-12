import type { Metadata } from "next";

import { Fold } from "@/components/ui/fold";
import { PageHeader } from "@/components/ui/page-header";
import { requireOwner } from "@/lib/auth";
import { feedProfile, type FollowSummary, type ProfileSummary } from "@/lib/feed/data";
import { ago, type Pillar } from "@/lib/feed/types";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Feed profile" };

// The learning engine in plain words (db/039 interest_profiles, follows), so
// the owner can SEE learning happen: what each feed has learned to like and
// skip, how much the day-one seeds still count, what is followed by tier,
// and how the profile moved rebuild by rebuild. Reads only, through RLS; the
// worker rebuilds at 04:00 Chicago (feed_profile).

const PILLAR_LABEL: Record<Pillar, string> = { home: "Home", sports: "Sports" };
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
            // "Not interested" wraps in a quarter of 390 px.
            ["Skipped", p.niCount],
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
        A story naming one of these ranks higher. Alabama is held near the top of For you; at most five tier-1 sports stories reach its first page.
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

export default async function FeedProfilePage() {
  await requireOwner();
  const supabase = await createClient();
  // Display only: the worker builds on the database clock.
  // eslint-disable-next-line react-hooks/purity
  const now = Date.now();
  const { latest, history, follows, pendingReactions } = await feedProfile(supabase);
  const pillars = (["home", "sports"] as const).filter((p) => latest[p]);

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

        <Follows follows={follows} />

        {history.length > 0 && (
          <Fold label="Rebuilds" count={history.length} testId="profile-history">
            {history.map((h) => (
              <li key={h.id} data-testid="profile-history-row" className="border-b border-border py-3 font-mono text-xs text-text-muted">
                <span className="text-text">{PILLAR_LABEL[h.pillar]}</span> · {ago(h.builtAt, now)} · loved {h.loveCount} · not
                interested {h.niCount} · opened {h.openCount} · seeds {pct(h.seedWeightLove)}
              </li>
            ))}
          </Fold>
        )}
      </div>
    </>
  );
}
