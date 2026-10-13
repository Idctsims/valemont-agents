import Link from "next/link";

import type { FeedStory } from "@/lib/feed/types";

import { FeedList } from "./feed-list";

// Home's feed (Chat 3 Phase 3 scope addition, owner 2026-10-12): For you's
// top HOME_TOP stories, the same cards and reactions (pillar home), then a
// link to /news, where the tabs, the category chips and every page live.

export function HomeFeed({ stories, now }: { stories: FeedStory[]; now: number }) {
  return (
    <section id="stories" aria-label="For you" data-testid="home-feed" className="mt-14 scroll-mt-6 lg:max-w-3xl">
      <h2 className="label-mono text-text-muted">For you</h2>
      {stories.length === 0 ? (
        <p data-testid="feed-empty" className="mt-3 border-t border-border pt-4 text-base text-text-muted">
          Nothing ranked yet. For you fills in after the worker&apos;s next profile rebuild.
        </p>
      ) : (
        <FeedList stories={stories} pillar="home" now={now} testId="feed-for-you" />
      )}
      <Link
        href="/news"
        data-testid="see-all-news"
        className="tap -ml-3 mt-2 inline-flex items-center rounded-pill px-3 text-sm font-medium text-accent transition-colors hover:bg-surface-2"
      >
        See all news →
      </Link>
    </section>
  );
}
