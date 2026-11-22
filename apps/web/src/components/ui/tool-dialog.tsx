"use client";

import { X } from "@phosphor-icons/react";
import { useEffect, useRef } from "react";

// A round icon button that opens a box over the page (owner, Nov 21: "exactly
// what we did on Game Day, where the magnifying glass opens the box"). The
// box is a native modal <dialog>: Escape and a tap on the backdrop close it,
// focus moves to its first input, and its contents stay mounted while closed,
// so a search typed earlier is still there when it opens again.

export function ToolDialog({ icon, label, testId, autoOpen = false, children }: {
  icon: React.ReactNode;
  label: string;
  testId: string;
  /** Open on the first render (a link that lands on this tool). */
  autoOpen?: boolean;
  children: React.ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);

  const open = () => {
    const d = dialog.current;
    if (!d) return;
    if (!d.open) d.showModal();
    d.querySelector<HTMLInputElement>("input")?.focus();
  };
  const close = () => dialog.current?.close();

  useEffect(() => {
    if (autoOpen) open();
  }, [autoOpen]);

  return (
    <>
      <button type="button" onClick={open} data-testid={`${testId}-open`} aria-label={label} title={label}
              className="tap inline-flex size-11 items-center justify-center rounded-pill border border-border bg-surface text-text hover:border-accent-ink">
        {icon}
      </button>
      <dialog ref={dialog} data-testid={testId} aria-label={label}
              onClick={(e) => { if (e.target === dialog.current) close(); }}
              className="m-0 mx-auto mt-4 w-full max-w-xl bg-transparent p-4 text-text backdrop:bg-bg/80 backdrop:backdrop-blur-sm sm:mt-16">
        <div className="relative max-h-tool overflow-y-auto rounded-inner border border-border bg-surface p-4">
          <button type="button" onClick={close} aria-label={`Close ${label.toLowerCase()}`} data-testid={`${testId}-close`}
                  className="tap absolute right-2 top-2 z-10 inline-flex size-10 items-center justify-center rounded-pill text-text-muted hover:text-text">
            <X size={20} aria-hidden />
          </button>
          {children}
        </div>
      </dialog>
    </>
  );
}
