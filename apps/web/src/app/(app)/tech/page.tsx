import type { Metadata } from "next";

import { headers } from "next/headers";
import Link from "next/link";

import { FeedSection } from "@/components/feed/feed-section";
import { ChipFace } from "@/components/ui/chip";
import { PageHeader } from "@/components/ui/page-header";
import { requireOwner } from "@/lib/auth";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { forYouStories, latestStories } from "@/lib/feed/data";
import { TECH_CHIPS, feedHref, pageFromParam, tabFromParam, techCategoryFromParam } from "@/lib/feed/types";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Tech Hub" };

// Tech Hub (Chat 4 Phase 1; docs/tech_hub.md, db/045): the tech pillar on the
// feed engine. Official release notes, changelogs and blogs (tagged
// "Official", listed the cycle they are found, headline first until the
// summary lands) and Hacker News. For you / Latest and the category chips,
// all kept in the URL. Tech's pillar ONLY, in both directions: no tech story
// reaches Home or /news, and no Home or Sports story reaches this page
// (feed_for_you and the database's pillar guard). Reactions here write
// pillar 'tech' and train only the tech profile.

type TechParams = {
  category?: string | string[];
  feed?: string | string[];
  latest?: string | string[];
  foryou?: string | string[];
};

export default async function TechPage({ searchParams }: { searchParams: Promise<TechParams> }) {
  await requireOwner();
  const supabase = await createClient();
  const params = await searchParams;
  const category = techCategoryFromParam(params.category);
  const tab = tabFromParam(params.feed);
  const page = pageFromParam(tab === "latest" ? params.latest : params.foryou);
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  const feed =
    tab === "latest"
      ? await latestStories(supabase, { pillar: "tech", page, withTests, category })
      : await forYouStories(supabase, { pillar: "tech", page, category });
  // Display only ("3h"): rendered per request.
  // eslint-disable-next-line react-hooks/purity
  const now = Date.now();

  return (
    <>
      <PageHeader
        label="Tech"
        title="Tech Hub"
        summary="Official releases from Anthropic, OpenAI and your stack, and what Hacker News is reading."
      />

      <nav aria-label="Category" data-testid="category-chips" className="-ml-1 flex flex-wrap items-center lg:max-w-3xl">
        {TECH_CHIPS.map((c) => {
          const selected = c.category === category;
          return (
            <Link
              key={c.label}
              href={feedHref("/tech", { tab, category: c.category, anchor: false })}
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
        pillar="tech"
        base="/tech"
        category={category}
      />
    </>
  );
}
