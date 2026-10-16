"use client";

import { useLayoutEffect, useRef, useState } from "react";

// Why a reference stayed unverified: muted, never danger red (red is for
// real losses only). Two lines at most, wrapped at words, with "more" when
// the reason runs longer; the toggle shows only when it is cut.

export function UnverifiedReason({ note }: { note: string | null }) {
  const ref = useRef<HTMLParagraphElement>(null);
  const [open, setOpen] = useState(false);
  const [cut, setCut] = useState(false);

  useLayoutEffect(() => {
    const el = ref.current;
    if (el && !open) setCut(el.scrollHeight > el.clientHeight + 1);
  }, [note, open]);

  return (
    <div className="mt-1">
      <p ref={ref} data-testid="unverified-reason"
         className={`text-sm text-text-muted text-pretty ${open ? "" : "line-clamp-2"}`}>
        Not checked: {note ?? "no reason recorded"}
      </p>
      {(cut || open) && (
        <button type="button" data-testid="unverified-more" aria-expanded={open} onClick={() => setOpen((o) => !o)}
                className="tap -ml-2 inline-flex items-center rounded-pill px-2 text-sm text-accent hover:bg-surface-2">
          {open ? "less" : "more"}
        </button>
      )}
    </div>
  );
}
