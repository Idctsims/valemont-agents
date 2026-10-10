"use client";

import { CaretDown } from "@phosphor-icons/react";
import Link from "next/link";
import { useId, useState } from "react";

import type { Brief } from "@/lib/brief/types";

import { AskWagsAboutThis, BriefLabel, OneMove, Sections } from "./brief-parts";

// Home's top block, above TODAY, when today's brief exists. The date stays
// Home's one hero; the headline sits one step down. "Read the brief" opens
// the sections inline; /brief is the full page.

export function BriefCard({ brief, startOpen = false }: { brief: Brief; startOpen?: boolean }) {
  const [open, setOpen] = useState(startOpen);
  const id = useId();
  return (
    <section aria-label="Morning brief" data-testid="brief-card" className="mb-12 lg:max-w-3xl">
      <BriefLabel brief={brief} />
      <h2 data-testid="brief-headline" className="mt-2 font-display text-2xl text-text text-balance lg:text-3xl">
        <Link href="/brief" className="transition-colors hover:text-accent">
          {brief.body.headline}
        </Link>
      </h2>
      <div className="mt-5">
        <OneMove text={brief.body.one_move} />
      </div>
      {open && (
        <div id={id} className="mt-8">
          <Sections sections={brief.body.sections} />
        </div>
      )}
      <div className="mt-4 -ml-3 flex flex-wrap items-center gap-1">
        {brief.body.sections.length > 0 && (
          <button
            type="button"
            aria-expanded={open}
            aria-controls={id}
            onClick={() => setOpen((o) => !o)}
            className="tap inline-flex items-center gap-2 rounded-pill px-3 text-sm text-text transition-colors hover:bg-surface-2"
          >
            {open ? "Hide the brief" : "Read the brief"}
            <CaretDown size={14} aria-hidden className={`transition-transform ${open ? "rotate-180" : ""}`} />
          </button>
        )}
        <AskWagsAboutThis />
      </div>
    </section>
  );
}
