"use client";

import { CaretRight } from "@phosphor-icons/react";
import { useId, useState } from "react";

/**
 * A ledger fold: one mono line, "Label · N", that opens on tap. Used for
 * the things kept but out of the way: moved-on goals, archived ventures,
 * parked workstreams, done dates. Closed points right, open points down.
 */
export function Fold({
  label,
  count,
  defaultOpen = false,
  testId,
  children,
}: {
  label: string;
  count: number;
  defaultOpen?: boolean;
  testId?: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <div data-testid={testId} className="border-b border-border">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((o) => !o)}
        className="tap flex w-full items-center justify-between font-mono text-xs text-text-muted transition-colors hover:text-text"
      >
        <span>
          {label} · {count}
        </span>
        {/* Reduced motion: tokens.css drops the transition, so it simply flips. */}
        <CaretRight size={16} aria-hidden className={`transition-transform ${open ? "rotate-90" : ""}`} />
      </button>
      {open && (
        <ul id={id} className="border-t border-border">
          {children}
        </ul>
      )}
    </div>
  );
}
