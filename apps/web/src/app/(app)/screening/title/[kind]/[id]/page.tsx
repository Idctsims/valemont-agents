import type { Metadata } from "next";

import Image from "next/image";
import { headers } from "next/headers";
import Link from "next/link";
import { notFound } from "next/navigation";

import { JustWatchAttribution, TmdbAttribution } from "@/components/screening/attribution";
import { Poster } from "@/components/screening/search-box";
import { ShelfButtons } from "@/components/screening/shelf-buttons";
import { Fold } from "@/components/ui/fold";
import { requireOwner } from "@/lib/auth";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { titleOnShelf } from "@/lib/screening/data";
import { isKind, longDate, yearOf } from "@/lib/screening/types";
import { createClient } from "@/lib/supabase/server";
import { titleDetails, type TitleDetails } from "@/lib/tmdb/client";
import { splitProviders, tmdbImage, type ProviderRow, type Providers } from "@/lib/tmdb/services";

export const metadata: Metadata = { title: "Screening Room" };

// One title (db/051): live from TMDB (server side), with its shelf from the
// database. The trailer plays inline (youtube-nocookie; the CSP allows that
// one frame origin). Where to stream: "Tonight on your services" first (a
// streaming offer on one of the owner's services), then the other streaming
// offers, rent and buy, folded. A test title (not on TMDB) renders from its
// database row, and only with the verified e2e marker.

export default async function TitlePage({ params }: { params: Promise<{ kind: string; id: string }> }) {
  await requireOwner();
  const { kind, id } = await params;
  const tmdbId = Number(id);
  if (!isKind(kind) || !Number.isSafeInteger(tmdbId) || tmdbId < 1) notFound();
  const supabase = await createClient();
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  const [live, shelf] = await Promise.all([titleDetails(kind, tmdbId), titleOnShelf(supabase, kind, tmdbId, { withTests })]);
  const t: TitleDetails | null = live ?? (shelf ? {
    tmdbId, kind, name: shelf.title.name, releaseDate: shelf.title.release_date, posterPath: shelf.title.poster_path,
    overview: null, genres: [], providers: (shelf.title.providers ?? {}) as Providers, trailerKey: null,
    nextEpisodeDate: shelf.title.next_episode_date, status: null,
  } : null);
  if (!t) notFound();
  const where = splitProviders(t.providers);
  const elsewhere = where.otherStream.length + where.rent.length + where.buy.length;

  return (
    <article data-testid="title-page" data-kind={t.kind} data-tmdb-id={t.tmdbId} className="lg:max-w-3xl">
      <p className="label-mono text-text-muted">
        <Link href="/screening" className="hover:text-text">Screening Room</Link> · {t.kind === "tv" ? "Series" : "Film"}
      </p>
      <div className="mt-4 flex flex-col gap-6 lg:flex-row">
        <Poster path={t.posterPath} name={t.name} large />
        <div className="min-w-0 flex-1">
          <h1 data-testid="title-name" className="font-display text-4xl text-text text-balance">{t.name}</h1>
          <p className="label-mono mt-2 text-text-muted">
            {yearOf(t.releaseDate) ?? "Date unknown"}
            {t.genres.length > 0 && ` · ${t.genres.slice(0, 3).join(", ")}`}
            {t.status && ` · ${t.status}`}
          </p>
          <p data-testid="title-date" className="mt-3 text-sm text-text">
            {t.kind === "tv"
              ? t.nextEpisodeDate ? `Next episode ${longDate(t.nextEpisodeDate)}` : "No episode scheduled"
              : t.releaseDate ? `US release ${longDate(t.releaseDate)}` : "No US release date yet"}
          </p>
          {t.overview && <p data-testid="title-overview" className="mt-4 text-base text-text-muted text-pretty">{t.overview}</p>}
          <div className="mt-5">
            <ShelfButtons target={shelf ? { titleId: shelf.title.id } : { kind: t.kind, tmdbId: t.tmdbId }} current={shelf?.status ?? null} />
          </div>
        </div>
      </div>

      {t.trailerKey && (
        <section aria-label="Trailer" data-testid="title-trailer" className="mt-8">
          <iframe
            src={`https://www.youtube-nocookie.com/embed/${encodeURIComponent(t.trailerKey)}`}
            title={`${t.name} trailer`}
            loading="lazy"
            allow="encrypted-media; picture-in-picture; fullscreen"
            allowFullScreen
            referrerPolicy="strict-origin-when-cross-origin"
            className="aspect-video w-full rounded-inner border border-border bg-surface-2"
          />
        </section>
      )}

      <section aria-label="Where to stream" data-testid="where-to-stream" className="mt-8">
        <h2 className="font-display text-2xl text-text">Tonight on your services</h2>
        {where.tonight.length === 0 ? (
          <p data-testid="tonight-none" className="mt-2 text-sm text-text-muted">Not streaming on Netflix, HBO Max, Paramount+, Starz or Prime Video in the US.</p>
        ) : (
          <ul data-testid="tonight" className="mt-3 flex flex-wrap gap-2">
            {where.tonight.map((p) => (
              <li key={p.service} data-testid="tonight-service" className="inline-flex items-center gap-2 rounded-pill border border-accent px-3 py-1 text-sm text-text">
                <Logo row={p} />
                {p.service}
                {p.kind !== "flatrate" && <span className="label-mono text-text-muted">{p.kind === "ads" ? "with ads" : "free"}</span>}
              </li>
            ))}
          </ul>
        )}
        {elsewhere > 0 && (
          <div data-testid="rent-buy" className="mt-4">
            <Fold label="Other ways to watch" count={elsewhere} testId="rent-buy-fold">
              {where.otherStream.length > 0 && <Group label="Other streaming" rows={where.otherStream} />}
              {where.rent.length > 0 && <Group label="Rent" rows={where.rent} />}
              {where.buy.length > 0 && <Group label="Buy" rows={where.buy} />}
            </Fold>
          </div>
        )}
        <JustWatchAttribution link={t.providers.link} />
      </section>
      <TmdbAttribution />
    </article>
  );
}

function Group({ label, rows }: { label: string; rows: ProviderRow[] }) {
  return (
    <li className="py-3">
      <p className="label-mono text-text-muted">{label}</p>
      <ul className="mt-2 flex flex-wrap gap-2">
        {rows.map((p) => (
          <li key={p.id} className="inline-flex items-center gap-2 rounded-pill border border-border px-3 py-1 text-sm text-text-muted">
            <Logo row={p} />
            {p.name}
          </li>
        ))}
      </ul>
    </li>
  );
}

function Logo({ row }: { row: ProviderRow }) {
  const src = tmdbImage(row.logo, "w45");
  return src ? <Image src={src} alt="" width={20} height={20} unoptimized className="size-5 rounded-inner" /> : null;
}
