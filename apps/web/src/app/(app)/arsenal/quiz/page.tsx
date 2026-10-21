import type { Metadata } from "next";

import { headers } from "next/headers";
import Link from "next/link";

import { ArsenalNav } from "@/components/arsenal/arsenal-nav";
import { QuizRunner } from "@/components/arsenal/quiz-runner";
import { PageHeader } from "@/components/ui/page-header";
import { QUIZ_MIN_SAVED } from "@/lib/arsenal/quiz";
import { ensureWeekQuiz } from "@/lib/arsenal/saved";
import { dropDate } from "@/lib/arsenal/types";
import { requireOwner } from "@/lib/auth";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Monday quiz" };

// The Monday quiz (Chat 5 Phase 2; docs/arsenal.md §9.6, db/059): this
// week's 10 questions on My Arsenal, built once on first open from the
// FSRS-due saved items, then resumed. No AI. Locked under 4 saved items.
// With the verified e2e marker it is a TEST quiz of test saves; ?fresh=1
// (marker only) builds a new test quiz, so a spec starts from question 1.

export default async function QuizPage({ searchParams }: { searchParams: Promise<{ fresh?: string }> }) {
  await requireOwner();
  const supabase = await createClient();
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  const fresh = withTests && (await searchParams).fresh === "1";
  const quiz = await ensureWeekQuiz(supabase, { withTests, freshTest: fresh });

  return (
    <>
      <PageHeader label="Culture · The Arsenal" title="Monday quiz"
                  summary={`The week of ${dropDate(quiz.weekStart)}: ten questions on what you saved, scheduled so the ones you miss come back sooner.`}>
        <ArsenalNav current="quiz" />
      </PageHeader>
      {quiz.state === "ready" ? (
        <QuizRunner key={quiz.id} quizId={quiz.id} questions={quiz.questions} initial={quiz.answers} />
      ) : (
        <div data-testid="quiz-locked" className="border-t border-border pt-4 lg:max-w-3xl">
          <p className="text-lg text-text">
            {quiz.state === "locked"
              ? `Save at least ${QUIZ_MIN_SAVED} to unlock the quiz.`
              : "Not enough to ask yet: the quiz needs three other answers to choose from for each question."}
          </p>
          <p className="mt-2 text-base text-text-muted">
            {quiz.saved} saved so far. Save cards from{" "}
            <Link href="/arsenal" className="text-accent underline decoration-border underline-offset-4">today&apos;s drop and Arm Me</Link>.
          </p>
        </div>
      )}
    </>
  );
}
