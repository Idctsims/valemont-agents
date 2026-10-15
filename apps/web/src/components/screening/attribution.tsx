// The attributions TMDB's and JustWatch's terms ask for (db/051).

import Image from "next/image";

/** TMDB: on /screening and every title page. Their official logo
 * (public/tmdb-logo.svg, added by the owner on 2026-10-14) links to TMDB,
 * with the notice their terms ask for. */
export function TmdbAttribution() {
  return (
    <p data-testid="tmdb-attribution" className="mt-12 flex max-w-measure items-center gap-3 border-t border-border pt-4 text-xs text-text-muted">
      <a href="https://www.themoviedb.org/" target="_blank" rel="noopener noreferrer" className="tap inline-flex shrink-0 items-center">
        <Image src="/tmdb-logo.svg" alt="TMDB" width={44} height={32} unoptimized data-testid="tmdb-logo" />
      </a>
      <span>This product uses the TMDB API but is not endorsed or certified by TMDB.</span>
    </p>
  );
}

/** JustWatch: wherever where-to-stream shows (TMDB's provider data is JustWatch's). */
export function JustWatchAttribution({ link }: { link?: string }) {
  return (
    <p data-testid="justwatch-attribution" className="mt-3 text-xs text-text-muted">
      Where to stream:{" "}
      <a href={link ?? "https://www.justwatch.com/"} target="_blank" rel="noopener noreferrer" className="text-text hover:text-accent">
        JustWatch
      </a>
      , via TMDB.
    </p>
  );
}
