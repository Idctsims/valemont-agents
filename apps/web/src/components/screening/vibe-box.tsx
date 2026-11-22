"use client";

import { Sparkle, UserCircle, X } from "@phosphor-icons/react";
import Image from "next/image";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import type { Anchor } from "@/lib/screening/vibe";
import type { Shelf } from "@/lib/screening/types";
import { tmdbImage } from "@/lib/tmdb/services";

import { Poster } from "./search-box";
import { ShelfButtons } from "./shelf-buttons";

// More like… (owner, 2026-11-21; docs/screening.md §8), in the sparkles
// button's box at the Screening Room's top right. Type a film, a
// series, a person or a phrase: TMDB suggests titles and people
// (/api/screening/anchors, 250 ms after the last keystroke); choosing one,
// or pressing Enter on the text, asks /api/screening/vibe for titles with
// the same vibe. Each card: the poster, Film or Series and year, Claude's
// reason, the owner's services, Seen / Loved / the shelf, and one-tap
// Want or Watching. A recent search is a chip (served from the stored row).

const DEBOUNCE_MS = 250;

type Suggestion =
  | { type: "title"; tmdbId: number; kind: "movie" | "tv"; name: string; year: number | null; posterPath: string | null }
  | { type: "person"; tmdbId: number; name: string; dept: string | null; knownFor: string[]; profilePath: string | null };

export type VibeResultCard = {
  tmdbId: number; kind: "movie" | "tv"; name: string; year: number | null; posterPath: string | null; reason: string;
  services: string[]; shelf: Shelf | null; seen: boolean; reaction: string | null;
};
export type RecentVibe = { key: string; label: string; anchor: Anchor };

function toAnchor(s: Suggestion): Anchor {
  return s.type === "title"
    ? { kind: s.kind, id: s.tmdbId, label: s.name, year: s.year }
    : { kind: "person", id: s.tmdbId, label: s.name, dept: s.dept };
}

export function VibeBox({ recent, initial }: { recent: RecentVibe[]; initial: Anchor | null }) {
  const [q, setQ] = useState("");
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ label: string; cached: boolean; cards: VibeResultCard[] } | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const latest = useRef("");
  const ran = useRef(false);

  const run = useCallback(async (anchor: Anchor) => {
    setOpen(false);
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/screening/vibe", {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ anchor }),
      });
      if (!res.ok) {
        setError((await res.text()) || "That didn't work. Try again.");
        return;
      }
      setResult(await res.json());
    } catch {
      setError("That didn't reach the server. Check the connection and try again.");
    } finally {
      setBusy(false);
    }
  }, []);

  // A title page's "More like this" lands here with its anchor: run it once.
  useEffect(() => {
    if (initial && !ran.current) {
      ran.current = true;
      void run(initial);
    }
  }, [initial, run]);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  // The suggestions close on Escape and on a tap outside the box.
  const formRef = useRef<HTMLFormElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (formRef.current && !formRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);

  const onType = (value: string) => {
    setQ(value);
    const text = value.trim();
    latest.current = text;
    window.clearTimeout(timer.current);
    if (text.length < 2) {
      setSuggestions([]);
      setOpen(false);
      return;
    }
    timer.current = window.setTimeout(async () => {
      try {
        const res = await fetch(`/api/screening/anchors?q=${encodeURIComponent(text)}`);
        const data = (await res.json()) as { results?: Suggestion[] };
        if (latest.current !== text) return;
        setSuggestions(data.results ?? []);
        setOpen(true);
      } catch {
        if (latest.current === text) setSuggestions([]);
      }
    }, DEBOUNCE_MS);
  };

  const choose = (s: Suggestion) => {
    setQ(s.type === "title" && s.year ? `${s.name} (${s.year})` : s.name);
    void run(toAnchor(s));
  };

  return (
    <section aria-label="More like" data-testid="vibe">
      <div className="pr-10">
        <h2 className="font-display text-2xl text-text">More like…</h2>
        <p className="label-mono mt-0.5 text-text-muted">a title, a person or a mood</p>
      </div>

      <form ref={formRef} role="search" className="relative mt-3" onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}
            onSubmit={(e) => {
        e.preventDefault();
        const text = q.trim();
        if (text.length >= 2) void run({ kind: "text", label: text });
      }}>
        <div className="flex items-center gap-2 rounded-pill border border-border bg-surface px-4 focus-within:border-accent-ink">
          <Sparkle size={18} aria-hidden className="shrink-0 text-text-muted" />
          <input type="text" inputMode="search" value={q} onChange={(e) => onType(e.target.value)}
                 onFocus={() => suggestions.length && setOpen(true)}
                 data-testid="vibe-input" aria-label="More like" placeholder="Notting Hill, Ridley Scott, 90s heist movies…"
                 autoComplete="off" autoCorrect="off" spellCheck={false} enterKeyHint="search" maxLength={120}
                 className="min-h-12 min-w-0 flex-1 bg-transparent text-base text-text outline-none placeholder:text-text-muted" />
          {q && (
            <button type="button" aria-label="Clear" onClick={() => { onType(""); setResult(null); setError(null); }}
                    className="tap inline-flex size-9 shrink-0 items-center justify-center rounded-pill text-text-muted hover:text-text">
              <X size={18} aria-hidden />
            </button>
          )}
        </div>

        {open && suggestions.length > 0 && (
          <ul data-testid="vibe-suggestions"
              className="mt-2 overflow-hidden rounded-inner border border-border bg-surface">
            {suggestions.map((s) => (
              <li key={`${s.type}:${s.type === "title" ? s.kind : "p"}:${s.tmdbId}`}>
                <button type="button" onClick={() => choose(s)} data-testid="vibe-suggestion" data-type={s.type}
                        className="flex w-full items-center gap-3 border-b border-border px-4 py-2.5 text-left hover:bg-band">
                  {s.type === "title" ? <Poster path={s.posterPath} name={s.name} small /> : <Face path={s.profilePath} name={s.name} />}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-base text-text">{s.name}</span>
                    <span className="label-mono block truncate text-text-muted">
                      {s.type === "title"
                        ? `${s.kind === "tv" ? "Series" : "Film"}${s.year ? ` · ${s.year}` : ""}`
                        : [s.dept, ...s.knownFor].filter(Boolean).join(" · ")}
                    </span>
                  </span>
                </button>
              </li>
            ))}
            <li>
              <button type="button" onClick={() => { const t = q.trim(); if (t.length >= 2) void run({ kind: "text", label: t }); }}
                      className="w-full px-4 py-3 text-left text-sm text-accent-ink hover:bg-band">
                Search the vibe of “{q.trim()}”
              </button>
            </li>
          </ul>
        )}
      </form>

      {recent.length > 0 && !busy && !result && (
        <div data-testid="vibe-recent" className="scroll-row mt-3 flex gap-2 overflow-x-auto">
          {recent.map((r) => (
            <button key={r.key} type="button" onClick={() => { setQ(r.label); void run(r.anchor); }}
                    className="tap shrink-0 whitespace-nowrap rounded-pill border border-border bg-surface px-3 text-sm text-text hover:border-accent-ink">
              {r.label}
            </button>
          ))}
        </div>
      )}

      <div aria-live="polite">
        {busy && (
          <div data-testid="vibe-busy" className="mt-5">
            <p className="text-sm text-text-muted">Finding the vibe… about five seconds.</p>
            <ul className="mt-3 border-t border-border" aria-hidden>
              {[0, 1, 2].map((i) => (
                <li key={i} className="flex animate-pulse gap-3 border-b border-border py-3">
                  <span className="aspect-poster w-16 shrink-0 rounded-inner bg-surface-2" />
                  <span className="flex flex-1 flex-col gap-2 pt-1">
                    <span className="h-4 w-2/3 rounded-pill bg-surface-2" />
                    <span className="h-3 w-1/3 rounded-pill bg-surface-2" />
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {error && !busy && <p role="alert" data-testid="vibe-error" className="mt-4 text-sm text-danger">{error}</p>}
        {result && !busy && (
          <div data-testid="vibe-results" data-cached={result.cached ? "true" : "false"} className="mt-5">
            <p className="label-mono text-text-muted">
              More like {result.label} · {result.cards.length} {result.cards.length === 1 ? "title" : "titles"}
              {result.cached ? " · saved search" : ""}
            </p>
            <ul className="mt-2 border-t border-border">
              {result.cards.map((c) => <VibeCard key={`${c.kind}:${c.tmdbId}`} c={c} />)}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

function Face({ path, name }: { path: string | null; name: string }) {
  const src = tmdbImage(path, "w92");
  if (!src) return <UserCircle size={40} aria-hidden className="shrink-0 text-text-muted" />;
  return <Image src={src} alt={`${name}`} width={92} height={138} unoptimized className="aspect-poster w-10 shrink-0 rounded-inner object-cover" />;
}

function VibeCard({ c }: { c: VibeResultCard }) {
  const mark = c.reaction === "love" ? "Loved" : c.seen ? "Seen" : c.shelf === "finished" ? "Finished" : null;
  return (
    <li data-testid="vibe-card" data-tmdb={`${c.kind}:${c.tmdbId}`} className="flex flex-wrap items-start gap-3 border-b border-border py-3">
      <Link href={`/screening/title/${c.kind}/${c.tmdbId}`} className="shrink-0"><Poster path={c.posterPath} name={c.name} /></Link>
      <div className="min-w-0 flex-1">
        <Link href={`/screening/title/${c.kind}/${c.tmdbId}`} className="block font-display text-lg leading-tight text-text hover:text-accent">
          {c.name}
        </Link>
        <p className="label-mono mt-0.5 text-text-muted">
          {c.kind === "tv" ? "Series" : "Film"}{c.year ? ` · ${c.year}` : ""}
        </p>
        <p data-testid="vibe-reason" className="mt-1 text-sm text-text">{c.reason}</p>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {c.services.map((s) => (
            <span key={s} data-testid="vibe-service"
                  className="label-mono inline-flex h-6 items-center rounded-pill bg-accent-strong px-2.5 text-on-accent-strong">
              {s}
            </span>
          ))}
          {mark && (
            <span data-testid="vibe-mark" className="label-mono inline-flex h-6 items-center rounded-pill border border-border px-2.5 text-text-muted">
              {mark}
            </span>
          )}
        </div>
      </div>
      <div className="w-full pl-19 sm:w-auto sm:pl-0">
        <ShelfButtons target={{ kind: c.kind, tmdbId: c.tmdbId }} current={c.shelf === "finished" ? "finished" : c.shelf} compact />
      </div>
    </li>
  );
}
