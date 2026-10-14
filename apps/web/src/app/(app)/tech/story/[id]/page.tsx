import type { Metadata } from "next";

import Link from "next/link";
import { notFound } from "next/navigation";

import { FeedList } from "@/components/feed/feed-list";
import { PageHeader } from "@/components/ui/page-header";
import { requireOwner } from "@/lib/auth";
import { techStory } from "@/lib/feed/data";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Tech story" };

// Where a free-credits push lands (worker feed/tech.py: "/tech/story/<id>").
// The one tech story, as its card on /tech: the original link, the summary
// (or the headline alone until it lands), the Official tag, and the three
// reactions, which write pillar 'tech'. A story that is not a real, kept tech
// story is a 404.

export default async function TechStoryPage({ params }: { params: Promise<{ id: string }> }) {
  await requireOwner();
  const id = Number((await params).id);
  if (!Number.isSafeInteger(id) || id < 1) notFound();
  const supabase = await createClient();
  const story = await techStory(supabase, id);
  if (!story) notFound();
  // Display only ("3h"): rendered per request.
  // eslint-disable-next-line react-hooks/purity
  const now = Date.now();

  return (
    <>
      <PageHeader label="Tech Hub" title="Story">
        <Link
          href="/tech"
          data-testid="story-to-tech"
          className="tap -ml-3 mt-2 inline-flex items-center rounded-pill px-3 text-sm font-medium text-accent transition-colors hover:bg-surface-2"
        >
          ← Tech Hub
        </Link>
      </PageHeader>
      <section aria-label="Story" data-testid="tech-story" className="lg:max-w-3xl">
        <FeedList stories={[story]} pillar="tech" now={now} testId="feed-story" />
      </section>
    </>
  );
}
