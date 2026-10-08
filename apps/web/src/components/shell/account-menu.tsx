"use client";

import { UserCircle } from "@phosphor-icons/react";
import { useEffect, useId, useRef, useState } from "react";

/**
 * Phone header menu. Log out lives here, behind a deliberate second tap at
 * the top of the screen, never as a one-tap button in the thumb zone.
 */
export function AccountMenu({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    root.current?.querySelector<HTMLElement>("[data-menu-panel] button")?.focus();
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        aria-label="Account"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
        className={`tap inline-flex items-center justify-center rounded-pill px-3 transition-colors hover:bg-surface-2 hover:text-text ${
          open ? "bg-surface-2 text-text" : "text-text-muted"
        }`}
      >
        <UserCircle size={22} aria-hidden />
      </button>
      {open && (
        <div
          id={panelId}
          data-menu-panel
          className="absolute top-full right-0 z-50 mt-2 min-w-44 rounded-inner border border-border bg-surface p-1"
        >
          {children}
        </div>
      )}
    </div>
  );
}
