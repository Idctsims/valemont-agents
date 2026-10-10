"use client";

import { useWags } from "./wags-provider";

/** Desktop: the rail's way into Wags. Opens the right-side panel. */
export function AskWagsButton() {
  const wags = useWags();
  return (
    <button
      type="button"
      onClick={wags.open}
      aria-expanded={wags.isOpen}
      data-testid="rail-wags"
      className="tap flex w-full items-center gap-3 rounded-pill border border-border px-3 text-sm text-text transition-colors hover:border-accent hover:text-accent"
    >
      <span
        aria-hidden
        className="flex size-7 items-center justify-center rounded-pill bg-accent font-display text-base text-on-accent"
      >
        W
      </span>
      Ask Wags
    </button>
  );
}
