"use client";

import { useState, useTransition } from "react";

import Link from "next/link";

import { answerSeen } from "@/app/(app)/screening/actions";
import type { PassTitle, SeenAnswer } from "@/lib/screening/recs";
import { yearOf } from "@/lib/screening/types";

import { Poster } from "./search-box";

// The seen-it pass (Chat 4 Phase 4; db/053): about 30 titles near the
// seeds, one at a time. "Seen, loved" feeds the profile; "Seen, not for me"
// counts as Not interested; both keep the title out of For you. "Haven't
// seen" changes nothing. Skip moves on without an answer. It resumes at the
// first title with no answer; from Settings it can be gone through again.
// Each answer saves in the background while the next title shows.

const ANSWERS: { a: SeenAnswer; label: string; testId: string; strong?: boolean }[] = [
  { a: "seen_loved", label: "Seen, loved", testId: "seen-loved", strong: true },
  { a: "seen_not_for_me", label: "Seen, not for me", testId: "seen-not-for-me" },
  { a: "not_seen", label: "Haven't seen", testId: "seen-not-seen" },
];

export function SeenPassFlow({ titles, start }: { titles: PassTitle[]; start: number }) {
  const [i, setI] = useState(start);
  const [answers, setAnswers] = useState<Record<number, SeenAnswer>>(
    () => Object.fromEntries(titles.filter((x) => x.answer).map((x) => [x.titleId, x.answer!])));
  const answered = titles.filter((x) => answers[x.titleId]).length;
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const t = titles[i];

  function answer(a: SeenAnswer) {
    const title = titles[i];
    setAnswers((prev) => ({ ...prev, [title.titleId]: a }));
    setI(i + 1);
    startTransition(async () => {
      try {
        const r = await answerSeen({ titleId: title.titleId, answer: a });
        setError(r.ok ? null : `${title.name}: ${r.error}`);
      } catch {
        setError(`${title.name}: that didn't save. Check the connection and try again.`);
      }
    });
  }

  return (
    <section aria-label="Seen it?" aria-busy={pending} data-testid="seen-pass" className="lg:max-w-xl">
      <p data-testid="seen-progress" className="label-mono text-text-muted">
        {answered} of {titles.length} answered{pending ? " · saving" : ""}
      </p>
      {error && <p role="alert" data-testid="seen-error" className="mt-2 text-sm text-danger">{error}</p>}
      {t ? (
        <article key={t.titleId} data-testid="seen-card" data-title-id={t.titleId} className="mt-4">
          <div className="flex gap-4">
            <div className="w-32 shrink-0 lg:w-40"><Poster path={t.posterPath} name={t.name} fill /></div>
            <div className="min-w-0">
              <h2 data-testid="seen-title" className="font-display text-3xl text-text text-balance">{t.name}</h2>
              <p className="label-mono mt-1 text-text-muted">
                {t.kind === "tv" ? "Series" : "Film"}
                {yearOf(t.releaseDate) ? ` · ${yearOf(t.releaseDate)}` : ""}
                {t.isTest && <span className="ml-2 rounded-pill border border-text-muted px-2 normal-case tracking-normal text-text">test</span>}
              </p>
              {answers[t.titleId] && (
                <p className="mt-2 text-sm text-text-muted">You said: {ANSWERS.find((x) => x.a === answers[t.titleId])?.label}</p>
              )}
              {t.overview && <p className="mt-3 line-clamp-4 text-sm text-text-muted text-pretty">{t.overview}</p>}
            </div>
          </div>
          <div role="group" aria-label="Have you seen it?" className="mt-6 flex flex-col gap-2">
            {ANSWERS.map(({ a, label, testId, strong }) => (
              <button key={a} type="button" data-testid={testId} onClick={() => answer(a)}
                      className={`tap inline-flex w-full items-center justify-center rounded-pill border px-5 text-base transition-colors ${
                        strong ? "border-accent bg-accent text-on-accent hover:bg-accent/90" : "border-border text-text hover:bg-surface-2"
                      }`}>
                {label}
              </button>
            ))}
          </div>
          <div className="mt-4 flex items-center justify-between">
            <button type="button" data-testid="seen-skip" onClick={() => setI(i + 1)}
                    className="tap -ml-3 inline-flex items-center rounded-pill px-3 text-sm text-text-muted hover:bg-surface-2 hover:text-text">
              Skip
            </button>
            <Link href="/screening" data-testid="seen-later"
                  className="tap -mr-3 inline-flex items-center rounded-pill px-3 text-sm text-text-muted hover:bg-surface-2 hover:text-text">
              Finish later
            </Link>
          </div>
        </article>
      ) : (
        <div data-testid="seen-done" className="mt-4">
          <p className="font-display text-3xl text-text">That&apos;s the pass.</p>
          <p className="mt-2 text-sm text-text-muted">
            {answered < titles.length
              ? `${titles.length - answered} skipped. They wait here for next time.`
              : "Every title answered. Your picks re-rank in about a minute."}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link href="/screening" className="tap inline-flex items-center rounded-pill bg-accent px-5 text-sm font-medium text-on-accent hover:bg-accent/90">
              Back to the Screening Room
            </Link>
            <Link href="/screening/seen?again=1" data-testid="seen-again"
                  className="tap inline-flex items-center rounded-pill border border-border px-5 text-sm text-text hover:bg-surface-2">
              Go through again
            </Link>
          </div>
        </div>
      )}
    </section>
  );
}
