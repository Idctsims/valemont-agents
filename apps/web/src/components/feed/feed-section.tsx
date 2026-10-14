import Link from "next/link";

import { feedHref, type FeedCategory, type FeedStory, type FeedTab, type Pillar, type Sport } from "@/lib/feed/types";

import { FeedList } from "./feed-list";

// A feed's two tabs: News (pillar home, at "/news", under the category chips),
// Sports News (pillar sports, at "/sports", under the sport chips) and Tech
// Hub (pillar tech, at "/tech", under the tech category chips; db/045). Home
// itself shows only For you's top 10 (components/feed/home-feed.tsx).
//   For you  the learned order (db/042 feed_for_you): the interest profile's
//            score minus recency plus any event boost; Alabama near the top;
//            at most 5 tier-1 sports stories on Home's page one, at most 6
//            per tier-1 follow on Sports' (9 in that follow's window). The
//            default.
//   Latest   newest first (Phase 1's order).
// Both leave out what the owner dismissed in this pillar. 30 a page; older
// pages are ?foryou=N or ?feed=latest&latest=N, the chip (?sport=, ?category=) kept,
// anchored to #stories.

export function FeedSection({
  tab,
  stories,
  page,
  hasMore,
  now,
  pillar = "home",
  base = "/news",
  sport = null,
  category = null,
}: {
  tab: FeedTab;
  stories: FeedStory[];
  page: number;
  hasMore: boolean;
  now: number;
  pillar?: Pillar;
  base?: "/news" | "/sports" | "/tech";
  sport?: Sport | null;
  category?: FeedCategory | null;
}) {
  const label = tab === "latest" ? "Latest" : "For you";
  const tabs: { tab: FeedTab; label: string }[] = [
    { tab: "for-you", label: "For you" },
    { tab: "latest", label: "Latest" },
  ];
  const href = (t: FeedTab, p = 1) => feedHref(base, { tab: t, page: p, sport, category });
  return (
    <section
      id="stories"
      aria-label="Feed"
      data-testid="feed"
      className="mt-6 scroll-mt-6 lg:max-w-3xl"
    >
      <h2 className="sr-only">
        {label}
        {page > 1 ? ` · page ${page}` : ""}
      </h2>
      {/* No negative margin: the active pill starts on the cards' left edge. */}
      <nav aria-label="Feed order" data-testid="feed-tabs" className="flex items-center gap-1">
        {tabs.map((t) => (
          <Link
            key={t.tab}
            href={href(t.tab)}
            data-testid={`feed-tab-${t.tab}`}
            aria-current={t.tab === tab ? "page" : undefined}
            className={`tap inline-flex items-center rounded-pill px-3 label-mono transition-colors ${
              t.tab === tab ? "bg-surface-2 text-text" : "text-text-muted hover:text-text"
            }`}
          >
            {t.label}
          </Link>
        ))}
        {page > 1 && <span className="label-mono ml-2 text-text-muted">Page {page}</span>}
      </nav>

      {stories.length === 0 ? (
        <p data-testid="feed-empty" className="mt-3 border-t border-border pt-4 text-base text-text-muted">
          {page > 1
            ? "Nothing older."
            : sport || category
              ? `Nothing in this ${sport ? "sport" : "category"} yet. All has every story.`
              : tab === "for-you"
                ? "Nothing ranked yet. For you fills in after the worker's next profile rebuild; Latest has every story now."
                : "No stories yet. They arrive with the worker's next feed run."}
        </p>
      ) : (
        <FeedList
          key={`${tab}-${page}-${sport ?? category ?? "all"}`}
          stories={stories}
          pillar={pillar}
          now={now}
          testId={tab === "latest" ? "feed-latest" : "feed-for-you"}
        />
      )}

      {(page > 1 || hasMore) && (
        <nav aria-label={`${label} pages`} className="mt-4 -ml-3 flex items-center gap-1">
          {page > 1 && (
            <Link href={href(tab, page - 1)} className="tap inline-flex items-center rounded-pill px-3 text-sm text-text transition-colors hover:bg-surface-2">
              {tab === "latest" ? "Newer" : "Back"}
            </Link>
          )}
          {hasMore && (
            <Link
              data-testid="feed-older"
              href={href(tab, page + 1)}
              className="tap inline-flex items-center rounded-pill px-3 text-sm text-text transition-colors hover:bg-surface-2"
            >
              {tab === "latest" ? "Older stories" : "More"}
            </Link>
          )}
        </nav>
      )}
    </section>
  );
}
