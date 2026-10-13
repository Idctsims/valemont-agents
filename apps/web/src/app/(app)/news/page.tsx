import type { Metadata } from "next";

import { headers } from "next/headers";
import Link from "next/link";

import { FeedSection } from "@/components/feed/feed-section";
import { ChipFace } from "@/components/ui/chip";
import { PageHeader } from "@/components/ui/page-header";
import { requireOwner } from "@/lib/auth";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { forYouStories, latestStories } from "@/lib/feed/data";
import { NEWS_CHIPS, categoryFromParam, feedHref, pageFromParam, tabFromParam } from "@/lib/feed/types";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "News" };

// News (Chat 3 Phase 3 scope addition, owner 2026-10-12): Home's whole feed,
// pillar home, with For you / Latest and the category chips
// (stories.category, db/042), all kept in the URL. Home's pillar ONLY: For
// you passes p_cross_cap 0, so no tier-1 sports story crosses in (db/043);
// Latest reads pillar home. Reactions here are Home
// reactions (pillar home) and train Home's profile. Sports has its own page.

type NewsParams = {
  category?: string | string[];
  feed?: string | string[];
  latest?: string | string[];
  foryou?: string | string[];
};

export default async function NewsPage({ searchParams }: { searchParams: Promise<NewsParams> }) {
  await requireOwner();
  const supabase = await createClient();
  const params = await searchParams;
  const category = categoryFromParam(params.category);
  const tab = tabFromParam(params.feed);
  const page = pageFromParam(tab === "latest" ? params.latest : params.foryou);
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  const feed =
    tab === "latest"
      ? await latestStories(supabase, { pillar: "home", page, withTests, category })
      : // Home's pillar only: no tier-1 sports cross-ins on /news (owner, 2026-10-13).
        await forYouStories(supabase, { pillar: "home", page, category, crossCap: 0 });
  // Display only ("3h"): rendered per request.
  // eslint-disable-next-line react-hooks/purity
  const now = Date.now();

  return (
    <>
      <PageHeader label="Home" title="News">
        <Link
          href="/sports"
          data-testid="news-to-sports"
          className="tap -ml-3 mt-2 inline-flex items-center rounded-pill px-3 text-sm font-medium text-accent transition-colors hover:bg-surface-2"
        >
          Sports →
        </Link>
      </PageHeader>

      <nav aria-label="Category" data-testid="category-chips" className="-ml-1 flex flex-wrap items-center lg:max-w-3xl">
        {NEWS_CHIPS.map((c) => {
          const selected = c.category === category;
          return (
            <Link
              key={c.label}
              href={feedHref("/news", { tab, category: c.category, anchor: false })}
              data-testid={`category-chip-${c.category ?? "all"}`}
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
        pillar="home"
        base="/news"
        category={category}
      />
    </>
  );
}
