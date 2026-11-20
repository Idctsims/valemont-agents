"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { startTransition } from "react";

import { button } from "@/components/ui/button";

/**
 * A page that throws while loading (a Supabase timeout, "JWT issued at
 * future") shows this inside the shell, with the dock still there, instead of
 * Next's bare error page: a dead end in the installed app (web polish audit,
 * 2026-11-20). Production redacts the message; the digest matches the server
 * log line.
 */
export default function PageError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const router = useRouter();

  return (
    <div role="alert" data-testid="page-error" className="max-w-measure">
      <p className="label-mono text-text-muted">Couldn&apos;t load</p>
      <h1 className="mt-3 font-display text-4xl text-text text-balance">This page didn&apos;t load.</h1>
      <p className="mt-4 text-lg text-text-muted text-pretty">
        Usually a slow or dropped connection to the database. Try again, or head back to Home.
      </p>
      {error.digest && <p className="label-mono mt-4 text-text-muted">Ref {error.digest}</p>}
      <div className="mt-8 flex flex-wrap gap-3">
        <button
          type="button"
          className={button.primary}
          onClick={() =>
            startTransition(() => {
              // Refetch the server render, then clear the boundary.
              router.refresh();
              reset();
            })
          }
        >
          Try again
        </button>
        <Link href="/" className={button.secondary}>
          Go to Home
        </Link>
      </div>
    </div>
  );
}
