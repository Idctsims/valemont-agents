// The attributions TMDB's and JustWatch's terms ask for (db/051).

/** TMDB: on /screening and every title page. The official logo is to be
 * dropped in as public/tmdb-logo.svg (owner; their site blocks scripted
 * downloads), until then the name links to TMDB. */
export function TmdbAttribution() {
  return (
    <p data-testid="tmdb-attribution" className="mt-12 max-w-measure border-t border-border pt-4 text-xs text-text-muted">
      <a href="https://www.themoviedb.org/" target="_blank" rel="noopener noreferrer"
         className="font-mono font-medium text-text hover:text-accent">TMDB</a>
      <span className="ml-2">This product uses the TMDB API but is not endorsed or certified by TMDB.</span>
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
