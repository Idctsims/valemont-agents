"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { UnitSwitch } from "@/components/betting/betting-store";

// /betting's sections (the approved mock's top bar): Board (and every game
// page under it), Parlays (was Today; Phase 4.5), Record; and the x / %
// switch beside them.

const SECTIONS = [
  { key: "board", label: "Board", href: "/betting" },
  { key: "parlays", label: "Parlays", href: "/betting/parlays" },
  { key: "record", label: "Record", href: "/betting/record" },
] as const;

function current(path: string): string {
  if (path.startsWith("/betting/parlays") || path.startsWith("/betting/today")) return "parlays";
  if (path.startsWith("/betting/record")) return "record";
  return "board";
}

export function BettingNav() {
  const on = current(usePathname() ?? "/betting");
  return (
    <div className="mb-6 flex items-end justify-between gap-4 border-b border-border">
      <nav aria-label="Betting sections" className="flex gap-6">
        {SECTIONS.map((s) => (
          <Link key={s.key} href={s.href} data-testid={`betting-tab-${s.key}`} aria-current={on === s.key ? "page" : undefined}
                className={`tap -mb-px inline-flex items-center border-b-2 text-base transition-colors ${
                  on === s.key ? "border-accent-ink text-text" : "border-transparent text-text-muted hover:text-text"}`}>
            {s.label}
          </Link>
        ))}
      </nav>
      <div className="pb-1.5"><UnitSwitch /></div>
    </div>
  );
}
