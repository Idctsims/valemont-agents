import Image from "next/image";
import Link from "next/link";
import { CaretLeft, Play, Sparkle } from "@phosphor-icons/react/ssr";

import { JustWatchAttribution, TmdbAttribution } from "@/components/screening/attribution";
import { Poster } from "@/components/screening/search-box";
import { TitleActions, TitleMore } from "@/components/screening/title-actions";
import { Fold } from "@/components/ui/fold";
import { longDate, yearOf, type Shelf } from "@/lib/screening/types";
import type { TitleDetails } from "@/lib/tmdb/client";
import { splitProviders, tmdbImage, type ProviderRow } from "@/lib/tmdb/services";

// One title as Letterboxd's film page (owner, 2026-11-22; docs/screening.md
// §9): the backdrop across the top with the back arrow and the "⋯" menu over
// it; the name, year and who made it, the trailer button and the runtime, the
// poster at the right; the tagline and overview; the buttons (Watch, Watching
// for a series, Like, Watchlist); More like this. Then only the trailer,
// Tonight on your services and Other ways to watch. The page
// (/screening/title/[kind]/[id]) loads the data; this draws it.

type Target = { titleId: number } | { kind: "movie" | "tv"; tmdbId: number };
type Reaction = "love" | "neutral" | "not_interested" | null;

export function TitleView({ t, shelf, reaction, target }: {
  t: TitleDetails; shelf: Shelf | null; reaction: Reaction; target: Target;
}) {
  const where = splitProviders(t.providers);
  const elsewhere = where.otherStream.length + where.rent.length + where.buy.length;
  const backdrop = tmdbImage(t.backdropPath, "w1280");
  const year = yearOf(t.releaseDate);

  return (
    <article data-testid="title-page" data-kind={t.kind} data-tmdb-id={t.tmdbId} className="lg:max-w-3xl">
      {/* The hero: the backdrop, fading into the page; the back arrow and the menu over it. */}
      <div data-testid="title-hero"
           className="relative -mx-4 -mt-8 aspect-video max-h-hero overflow-hidden bg-surface-2 lg:mx-0 lg:mt-0 lg:rounded-card">
        {backdrop && (
          <Image src={backdrop} alt="" fill unoptimized priority sizes="(min-width: 1024px) 48rem, 100vw"
                 data-testid="title-backdrop" className="object-cover" />
        )}
        <div aria-hidden className="absolute inset-0 bg-linear-to-t from-bg via-bg/20 to-transparent" />
        <Link href="/screening" aria-label="Back to the Screening Room" data-testid="title-back"
              className="tap absolute left-4 top-4 inline-flex size-11 items-center justify-center rounded-pill bg-bg/70 text-text backdrop-blur-sm hover:bg-bg">
          <CaretLeft size={22} weight="bold" aria-hidden />
        </Link>
        <div className="absolute right-4 top-4"><TitleMore target={target} reaction={reaction} /></div>
      </div>

      <div className="relative -mt-12 flex items-start gap-4 lg:-mt-16">
        <div className="min-w-0 flex-1 pt-14 lg:pt-18">
          <h1 data-testid="title-name" className="text-3xl font-semibold leading-tight text-text text-balance">{t.name}</h1>
          <p className="label-mono mt-3 text-text-muted">
            {year ?? "Date unknown"} · {t.kind === "tv" ? "Series" : "Film"}
            {t.makers && <> · {t.makers.label}</>}
          </p>
          {t.makers && <p data-testid="title-makers" className="text-lg font-semibold text-text">{t.makers.names.join(", ")}</p>}
          <div className="mt-4 flex flex-wrap items-center gap-4">
            {t.trailerKey && (
              <a href="#trailer" data-testid="title-trailer-button"
                 className="tap label-mono inline-flex items-center gap-2 rounded-tile bg-surface-2 px-3 text-text hover:bg-band">
                <Play size={14} weight="fill" aria-hidden /> Trailer
              </a>
            )}
            {t.runtime && (
              <span data-testid="title-runtime" className="text-base text-text-muted">
                {t.runtime} mins{t.kind === "tv" ? " an episode" : ""}
              </span>
            )}
          </div>
        </div>
        <div className="w-28 shrink-0 lg:w-40"><Poster path={t.posterPath} name={t.name} fill /></div>
      </div>

      {t.tagline && <p data-testid="title-tagline" className="label-mono mt-6 text-text-muted">{t.tagline}</p>}
      {t.overview && <p data-testid="title-overview" className="mt-2 text-base text-text-muted text-pretty">{t.overview}</p>}
      <p data-testid="title-date" className="mt-3 text-sm text-text-muted">
        {t.kind === "tv"
          ? t.nextEpisodeDate ? `Next episode ${longDate(t.nextEpisodeDate)}` : "No episode scheduled"
          : t.releaseDate ? `US release ${longDate(t.releaseDate)}` : "No US release date yet"}
      </p>

      <div className="mt-6">
        <TitleActions target={target} kind={t.kind} shelf={shelf}
                      reaction={reaction} />
      </div>
      {/* More like… with this title as the anchor (owner, Nov 21; docs/screening.md §8). */}
      <Link data-testid="title-more-like"
            href={`/screening?like=${t.kind}:${t.tmdbId}&label=${encodeURIComponent(t.name)}${year ? `&year=${year}` : ""}`}
            className="tap mt-3 inline-flex w-fit items-center gap-2 rounded-pill border border-border px-4 text-sm text-text hover:border-accent-ink">
        <Sparkle size={16} aria-hidden className="text-text-muted" /> More like this
      </Link>

      {t.trailerKey && (
        <section id="trailer" aria-label="Trailer" data-testid="title-trailer" className="mt-10 scroll-mt-6">
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

      <section aria-label="Where to stream" data-testid="where-to-stream" className="mt-10">
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
