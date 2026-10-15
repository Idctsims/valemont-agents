"use client";

import { MagnifyingGlass } from "@phosphor-icons/react";
import Image from "next/image";
import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";

import { tmdbImage } from "@/lib/tmdb/services";

import { ShelfButtons } from "./shelf-buttons";

// The Screening Room's search (db/051): typed into, debounced, answered by the
// server route /api/screening/search (TMDB, server side). Each result: the
// poster, the year, film or series, a link to its title page, and one-tap
// Want / Watching.

const DEBOUNCE_MS = 250;

type Hit = { tmdbId: number; kind: "movie" | "tv"; name: string; year: number | null; posterPath: string | null };

export function SearchBox() {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const id = useId();

  // Under 2 characters the box is idle: nothing shown, nothing asked.
  const active = q.trim().length >= 2;

  useEffect(() => {
    const query = q.trim();
    if (query.length < 2) {
      abort.current?.abort();
      return;
    }
    const t = setTimeout(async () => {
      abort.current?.abort();
      const ctl = new AbortController();
      abort.current = ctl;
      setState("loading");
      try {
        const res = await fetch(`/api/screening/search?q=${encodeURIComponent(query)}`, { signal: ctl.signal });
        const body = (await res.json()) as { results?: Hit[]; error?: string };
        if (!res.ok) throw new Error(body.error ?? "Search failed.");
        setHits(body.results ?? []);
        setError(null);
        setState("done");
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        setError((e as Error).message || "Search failed.");
        setState("error");
      }
    }, DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [q]);

  return (
    <section aria-label="Search" data-testid="screening-search" className="lg:max-w-3xl">
      <label htmlFor={id} className="label-mono text-text-muted">Find a show or film</label>
      <div className="mt-2 flex items-center gap-2 rounded-pill border border-border bg-surface px-4">
        <MagnifyingGlass size={18} aria-hidden className="text-text-muted" />
        <input
          id={id}
          type="search"
          inputMode="search"
          autoComplete="off"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Succession, Dune, The Bear…"
          data-testid="screening-search-input"
          className="tap w-full bg-transparent text-base text-text placeholder:text-text-muted focus:outline-none"
        />
      </div>
      <div aria-live="polite" aria-busy={active && state === "loading"} data-testid="screening-search-state"
           data-state={active ? state : "idle"}>
        {active && state === "error" && <p role="alert" className="mt-3 text-sm text-danger">{error}</p>}
        {active && state === "done" && hits.length === 0 && <p className="mt-3 text-sm text-text-muted">Nothing found.</p>}
        {active && hits.length > 0 && (
          <ul data-testid="screening-search-results" className="mt-3 border-t border-border">
            {hits.map((h) => (
              <li key={`${h.kind}-${h.tmdbId}`} data-testid="screening-search-result" data-kind={h.kind}
                  className="flex items-center gap-3 border-b border-border py-2">
                <Poster path={h.posterPath} name={h.name} small />
                <div className="min-w-0 flex-1">
                  <Link href={`/screening/title/${h.kind}/${h.tmdbId}`} className="block truncate font-display text-lg text-text hover:text-accent">
                    {h.name}
                  </Link>
                  <p className="label-mono text-text-muted">
                    {h.kind === "tv" ? "Series" : "Film"}{h.year ? ` · ${h.year}` : ""}
                  </p>
                </div>
                <ShelfButtons target={{ kind: h.kind, tmdbId: h.tmdbId }} current={null} compact />
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

/** A TMDB poster, or a blank tile of the same size. */
export function Poster({ path, name, small = false, large = false, fill = false }: {
  path: string | null; name: string; small?: boolean; large?: boolean; fill?: boolean;
}) {
  const src = tmdbImage(path, large || fill ? "w342" : small ? "w92" : "w185");
  const size = fill ? "w-full" : large ? "w-40 lg:w-56" : small ? "w-10" : "w-16";
  if (!src) return <div aria-hidden className={`${size} aspect-poster shrink-0 rounded-inner bg-surface-2`} />;
  return (
    <Image
      src={src}
      alt={`${name} poster`}
      width={large || fill ? 342 : small ? 92 : 185}
      height={large || fill ? 513 : small ? 138 : 278}
      unoptimized
      className={`${size} aspect-poster shrink-0 rounded-inner object-cover`}
    />
  );
}
