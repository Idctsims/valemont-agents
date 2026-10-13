import type { Metadata } from "next";

import { headers } from "next/headers";
import Link from "next/link";

import { FeedSection } from "@/components/feed/feed-section";
import { ChipFace } from "@/components/ui/chip";
import { PageHeader } from "@/components/ui/page-header";
import { requireOwner } from "@/lib/auth";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { boostsNow, forYouStories, latestStories } from "@/lib/feed/data";
import { SPORT_CHIPS, feedHref, pageFromParam, sportFromParam, tabFromParam } from "@/lib/feed/types";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Sports News" };

// Sports News (Chat 3 Phase 3; docs/sports_news.md): the sports pillar on the
// same engine as Home. Chips filter by stories.sport (All shows stories with
// no sport too), kept in the URL with the tab and page. Reactions here write
// pillar 'sports' and train only the sports profile.

type SportsParams = {
  sport?: string | string[];
  feed?: string | string[];
  latest?: string | string[];
  foryou?: string | string[];
};

export default async function SportsPage({ searchParams }: { searchParams: Promise<SportsParams> }) {
  await requireOwner();
  const supabase = await createClient();
  const params = await searchParams;
  const sport = sportFromParam(params.sport);
  const tab = tabFromParam(params.feed);
  const page = pageFromParam(tab === "latest" ? params.latest : params.foryou);
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  // Display only ("3h", the boost list): rendered per request.
  // eslint-disable-next-line react-hooks/purity
  const now = Date.now();
  const [feed, boosts] = await Promise.all([
    tab === "latest"
      ? latestStories(supabase, { pillar: "sports", page, withTests, sport })
      : forYouStories(supabase, { pillar: "sports", page, sport }),
    boostsNow(supabase, new Date(now).toISOString()),
  ]);

  return (
    <>
      <PageHeader label="Sports" title="Sports News">
        {boosts.length > 0 && (
          <p data-testid="sports-boosts" className="mt-3 font-mono text-xs text-text-muted">
            Boosted now · {boosts.map((b) => b.label).join(" · ")}
          </p>
        )}
      </PageHeader>

      <nav aria-label="Sport" data-testid="sport-chips" className="-ml-1 flex flex-wrap items-center lg:max-w-3xl">
        {SPORT_CHIPS.map((c) => {
          const selected = c.sport === sport;
          return (
            <Link
              key={c.label}
              href={feedHref("/sports", { tab, sport: c.sport, anchor: false })}
              data-testid={`sport-chip-${c.sport ?? "all"}`}
              aria-current={selected ? "page" : undefined}
              className="tap group inline-flex items-center justify-center px-1"
            >
              <ChipFace selected={selected}>{c.label}</ChipFace>
            </Link>
          );
        })}
      </nav>

      <FeedSection
        tab={tab}
        stories={feed.stories}
        page={page}
        hasMore={feed.hasMore}
        now={now}
        pillar="sports"
        base="/sports"
        sport={sport}
      />
    </>
  );
}
