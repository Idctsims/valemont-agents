import type { Metadata } from "next";

import { headers } from "next/headers";
import Link from "next/link";

import { FeedList } from "@/components/feed/feed-list";
import { TmdbAttribution } from "@/components/screening/attribution";
import { ForYouRow } from "@/components/screening/for-you";
import { Poster, SearchBox } from "@/components/screening/search-box";
import { SeenBanner } from "@/components/screening/seen-banner";
import { ShelfButtons } from "@/components/screening/shelf-buttons";
import { PageHeader } from "@/components/ui/page-header";
import { requireOwner } from "@/lib/auth";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { latestStories, storiesInOrder } from "@/lib/feed/data";
import { PAGE_SIZE, pageFromParam } from "@/lib/feed/types";
import { shelves, upcoming } from "@/lib/screening/data";
import { forYou, seenPassProgress, trailersForYouIds } from "@/lib/screening/recs";
import {
  FINISHED_SHOWN, UPCOMING_DAYS, screeningTabFromParam, shortDate, trailerViewFromParam, yearOf, type ScreeningTab,
  type ShelfTitle, type TrailerView,
} from "@/lib/screening/types";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Screening Room" };

// The Screening Room (Chat 4 Phases 3-4; docs/screening.md, db/051, db/053).
//   Shelves   For you (12 picks from your taste, each with the loved title it
//             is like and where it streams tonight; the seen-it pass banner
//             while it has titles left), search (TMDB, server side),
//             Upcoming (a release or next episode in the next 30 days on Want
//             or Watching), then Watching, Want and Finished (the last 20).
//   Trailers  For you (ranked by the screening profile; db/053) or Latest
//             (newest first). Love / Neutral / Not interested write pillar
//             screening only (feed_pillar_guard).
// A test title or trailer shows only to a request with the verified e2e
// marker. TMDB's attribution closes the page.

type Params = { tab?: string | string[]; page?: string | string[]; view?: string | string[] };

export default async function ScreeningPage({ searchParams }: { searchParams: Promise<Params> }) {
  await requireOwner();
  const supabase = await createClient();
  const params = await searchParams;
  const tab = screeningTabFromParam(params.tab);
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  // Display only ("3h"): rendered per request.
  // eslint-disable-next-line react-hooks/purity
  const now = Date.now();

  return (
    <>
      <PageHeader label="Screening" title="Screening Room" summary="What you're watching, what you want to, where it streams tonight, and new trailers." />
      <Tabs tab={tab} />
      {tab === "trailers" ? (
        <Trailers page={pageFromParam(params.page)} view={trailerViewFromParam(params.view)} withTests={withTests} now={now}
                  supabase={supabase} />
      ) : (
        <Shelves withTests={withTests} supabase={supabase} />
      )}
      <TmdbAttribution />
    </>
  );
}

function Tabs({ tab }: { tab: ScreeningTab }) {
  const tabs: { tab: ScreeningTab; label: string; href: string }[] = [
    { tab: "shelves", label: "Shelves", href: "/screening" },
    { tab: "trailers", label: "Trailers", href: "/screening?tab=trailers" },
  ];
  return (
    <nav aria-label="Screening Room" data-testid="screening-tabs" className="mb-6 flex items-center gap-1">
      {tabs.map((t) => (
        <Link key={t.tab} href={t.href} data-testid={`screening-tab-${t.tab}`} aria-current={t.tab === tab ? "page" : undefined}
              className={`tap inline-flex items-center rounded-pill px-3 label-mono transition-colors ${
                t.tab === tab ? "bg-surface-2 text-text" : "text-text-muted hover:text-text"
              }`}>
          {t.label}
        </Link>
      ))}
    </nav>
  );
}

async function Shelves({ withTests, supabase }: { withTests: boolean; supabase: Awaited<ReturnType<typeof createClient>> }) {
  const [s, recs, pass] = await Promise.all([
    shelves(supabase, { withTests }), forYou(supabase, { withTests }), seenPassProgress(supabase, { withTests }),
  ]);
  const soon = upcoming(s.all);
  return (
    <>
      <SeenBanner left={pass.total - pass.answered} total={pass.total} />
      <section aria-label="For you" data-testid="for-you" className="mb-10">
        <div className="flex items-baseline justify-between gap-3 lg:max-w-3xl">
          <h2 className="font-display text-2xl text-text">For you</h2>
          <span className="label-mono text-text-muted">from what you love</span>
        </div>
        <ForYouRow recs={recs} />
      </section>
      <SearchBox />
      <section aria-label="Upcoming" data-testid="upcoming" className="mt-10 lg:max-w-3xl">
        <h2 className="label-mono text-text-muted">Upcoming · next {UPCOMING_DAYS} days</h2>
        {soon.length === 0 ? (
          <p className="mt-2 text-sm text-text-muted">Nothing on your Want or Watching shelves comes out in the next {UPCOMING_DAYS} days.</p>
        ) : (
          <ul className="mt-2 flex gap-3 overflow-x-auto pb-2">
            {soon.map((u) => (
              <li key={u.titleId} data-testid="upcoming-item" className="w-28 shrink-0">
                <Link href={`/screening/title/${u.kind}/${u.tmdbId}`} className="group block">
                  <Poster path={u.posterPath} name={u.name} />
                  <p className="mt-1 truncate text-sm text-text group-hover:text-accent">{u.name}</p>
                  <p className="label-mono text-text-muted">
                    {u.what === "episode" ? "New episode" : "Out"} {shortDate(u.on)}
                  </p>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
      <ShelfSection name="Watching" shelf="watching" rows={s.watching} empty="Nothing on. Search above, then tap Watching." />
      <ShelfSection name="Want" shelf="want" rows={s.want} empty="Nothing saved for later yet." />
      <ShelfSection name={s.finishedTotal > FINISHED_SHOWN ? `Finished · last ${FINISHED_SHOWN}` : "Finished"} shelf="finished"
                    rows={s.finished} empty="Nothing finished yet." />
    </>
  );
}

function ShelfSection({ name, shelf, rows, empty }: { name: string; shelf: string; rows: ShelfTitle[]; empty: string }) {
  return (
    <section aria-label={name} data-testid={`shelf-section-${shelf}`} className="mt-10 lg:max-w-3xl">
      <h2 className="font-display text-2xl text-text">{name}</h2>
      {rows.length === 0 ? (
        <p className="mt-2 text-sm text-text-muted">{empty}</p>
      ) : (
        <ul className="mt-3 border-t border-border">
          {rows.map((r) => (
            <li key={r.titleId} data-testid="shelf-title" data-title-id={r.titleId}
                className="flex flex-wrap items-center gap-3 border-b border-border py-3">
              <Poster path={r.posterPath} name={r.name} small />
              <div className="min-w-0 flex-1">
                <Link href={`/screening/title/${r.kind}/${r.tmdbId}`} className="block truncate font-display text-lg text-text hover:text-accent">
                  {r.name}
                </Link>
                <p className="label-mono text-text-muted">
                  {r.kind === "tv" ? "Series" : "Film"}
                  {yearOf(r.releaseDate) ? ` · ${yearOf(r.releaseDate)}` : ""}
                  {r.finishedAt ? ` · finished ${shortDate(r.finishedAt.slice(0, 10))}` : ""}
                  {r.isTest && <span className="ml-2 rounded-pill border border-text-muted px-2 normal-case tracking-normal text-text">test</span>}
                </p>
              </div>
              <ShelfButtons target={{ titleId: r.titleId }} current={r.status} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

async function Trailers({ page, view, withTests, now, supabase }: {
  page: number; view: TrailerView; withTests: boolean; now: number; supabase: Awaited<ReturnType<typeof createClient>>;
}) {
  const feed = view === "latest"
    ? await latestStories(supabase, { pillar: "screening", page, withTests })
    : await storiesInOrder(supabase, {
      pillar: "screening", ids: await trailersForYouIds(supabase, page, PAGE_SIZE, { withTests }), withTests,
    });
  const base = `/screening?tab=trailers${view === "latest" ? "&view=latest" : ""}`;
  const views: { view: TrailerView; label: string; href: string }[] = [
    { view: "for_you", label: "For you", href: "/screening?tab=trailers" },
    { view: "latest", label: "Latest", href: "/screening?tab=trailers&view=latest" },
  ];
  return (
    <section aria-label="Trailers" data-testid="trailers" data-view={view} className="lg:max-w-3xl">
      <nav aria-label="Trailer order" data-testid="trailer-views" className="-ml-3 mb-2 flex items-center gap-1">
        {views.map((v) => (
          <Link key={v.view} href={v.href} data-testid={`trailer-view-${v.view}`} aria-current={v.view === view ? "page" : undefined}
                className={`tap inline-flex items-center rounded-pill px-3 text-sm transition-colors ${
                  v.view === view ? "text-text underline decoration-accent decoration-2 underline-offset-8" : "text-text-muted hover:text-text"
                }`}>
            {v.label}
          </Link>
        ))}
      </nav>
      <p className="label-mono text-text-muted">
        {view === "latest" ? "Newest first · from your services, the studios and TMDB" : "Ranked by your taste · the last 60 days"}
      </p>
      {feed.stories.length === 0 ? (
        <p data-testid="feed-empty" className="mt-3 border-t border-border pt-4 text-base text-text-muted">
          {page > 1 ? "Nothing more." : "No trailers yet. They arrive with the feed runs and the nightly recommendations run."}
        </p>
      ) : (
        <FeedList key={`trailers-${view}-${page}`} stories={feed.stories} pillar="screening" now={now}
                  testId={view === "latest" ? "feed-latest" : "trailers-for-you"} />
      )}
      {(page > 1 || feed.hasMore) && (
        <nav aria-label="Trailer pages" className="mt-4 -ml-3 flex items-center gap-1">
          {page > 1 && (
            <Link href={`${base}${page > 2 ? `&page=${page - 1}` : ""}`}
                  className="tap inline-flex items-center rounded-pill px-3 text-sm text-text transition-colors hover:bg-surface-2">
              {view === "latest" ? "Newer" : "Back"}
            </Link>
          )}
          {feed.hasMore && (
            <Link href={`${base}&page=${page + 1}`} data-testid="feed-older"
                  className="tap inline-flex items-center rounded-pill px-3 text-sm text-text transition-colors hover:bg-surface-2">
              {view === "latest" ? "Older trailers" : "More trailers"}
            </Link>
          )}
        </nav>
      )}
    </section>
  );
}
