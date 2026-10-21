"use client";

import { useState, useTransition } from "react";

import { answerQuestion } from "@/app/(app)/arsenal/actions";
import { button } from "@/components/ui/button";
import { KIND_ASK, type QuestionView } from "@/lib/arsenal/quiz";
import type { QuizAnswer } from "@/lib/arsenal/saved";

// The Monday quiz (Chat 5 Phase 2; docs/arsenal.md §9.6): one question at a
// time, four options, no AI. An answer is recorded at once (one review row,
// with ts-fsrs's next card) and shows right or wrong before Next. Re-opening
// resumes at the first unanswered question. The end shows the score and a
// review list: each question, the right answer, and yours.

export function QuizRunner({ quizId, questions, initial }: {
  quizId: number;
  questions: QuestionView[];
  initial: Record<number, QuizAnswer>;
}) {
  const [answers, setAnswers] = useState<Record<number, QuizAnswer>>(initial);
  const firstOpen = () => questions.find((q) => !answers[q.no])?.no ?? null;
  const [current, setCurrent] = useState<number | null>(firstOpen);
  const [busy, start] = useTransition();
  const [error, setError] = useState<string | null>(null);

  const q = current ? questions[current - 1] : null;
  const given = q ? answers[q.no] : undefined;

  function choose(i: number) {
    if (!q || given || busy) return;
    start(async () => {
      try {
        const res = await answerQuestion({ quizId, no: q.no, chosen: i });
        if (!res.ok) {
          setError(res.error);
          return;
        }
        setError(null);
        setAnswers((a) => ({ ...a, [q.no]: { chosen: i, correct: res.correct, answer: res.answer } }));
      } catch {
        setError("That answer didn't save. Check the connection and try again.");
      }
    });
  }

  function next() {
    const after = questions.find((x) => x.no > (current ?? 0) && !answers[x.no]) ?? questions.find((x) => !answers[x.no]);
    setCurrent(after?.no ?? null);
  }

  if (!q) {
    const right = questions.filter((x) => answers[x.no]?.correct).length;
    return (
      <section data-testid="quiz-done" aria-label="Quiz results" className="lg:max-w-3xl">
        <p className="label-mono text-text-muted">This week</p>
        <p data-testid="quiz-score" className="mt-2 font-display text-5xl leading-none text-text">
          {right} of {questions.length}
        </p>
        <ol data-testid="quiz-review" className="mt-8 border-t border-border">
          {questions.map((x) => {
            const a = answers[x.no];
            return (
              <li key={x.no} data-testid="quiz-review-item" data-correct={a?.correct ?? false}
                  className="border-b border-border py-4">
                <p className="label-mono text-text-muted">{x.no}. {KIND_ASK[x.kind]}</p>
                <p className="mt-1 text-base text-text text-pretty">{x.prompt}</p>
                {a && (
                  <>
                    <p className="mt-2 text-sm text-text text-pretty">
                      <span className="label-mono mr-2 text-text-muted">Answer</span>{x.options[a.answer]}
                    </p>
                    {!a.correct && (
                      <p className="mt-1 text-sm text-text-muted text-pretty">
                        <span className="label-mono mr-2">Yours</span>{x.options[a.chosen]}
                      </p>
                    )}
                  </>
                )}
              </li>
            );
          })}
        </ol>
      </section>
    );
  }

  return (
    <section data-testid="quiz" aria-label="Quiz" aria-busy={busy} className="lg:max-w-3xl">
      <p data-testid="quiz-progress" className="label-mono text-text-muted">
        Question {q.no} of {questions.length}
      </p>
      <h2 className="mt-3 text-lg text-text-muted">{KIND_ASK[q.kind]}</h2>
      <p data-testid="quiz-prompt"
         className={`mt-2 text-text text-pretty ${q.kind === "fits" ? "text-xl" : "font-display text-4xl leading-tight"}`}>
        {q.prompt}
      </p>
      <ul className="mt-6 space-y-2">
        {q.options.map((o, i) => {
          const state = !given ? "open" : i === given.answer ? "right" : i === given.chosen ? "wrong" : "other";
          return (
            <li key={i}>
              <button type="button" data-testid="quiz-option" data-state={state} disabled={!!given || busy}
                      onClick={() => choose(i)}
                      className={`tap flex w-full items-center justify-between gap-3 rounded-inner border px-4 py-3 text-left text-base text-pretty transition-colors ${
                        state === "right" ? "border-accent text-accent"
                          : state === "wrong" ? "border-text-muted text-text-muted line-through"
                            : state === "other" ? "border-border text-text-muted"
                              : "border-border text-text hover:bg-surface-2"
                      }`}>
                <span>{o}</span>
                {/* Said in words, not only by colour (owner, Oct 21). */}
                {state === "right" && (
                  <span data-testid="quiz-right-label" className="label-mono shrink-0 text-accent">Right answer</span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
      {error && <p role="alert" data-testid="quiz-error" className="mt-3 text-sm text-danger">{error}</p>}
      {given && (
        <div className="mt-6 flex items-center gap-4">
          <p data-testid="quiz-verdict" role="status" className="text-base text-text">{given.correct ? "Right." : "Not this time."}</p>
          <button type="button" data-testid="quiz-next" onClick={next} className={button.primary}>
            {questions.some((x) => !answers[x.no]) ? "Next" : "See the score"}
          </button>
        </div>
      )}
    </section>
  );
}
