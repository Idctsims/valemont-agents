"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { builtGroups, isActive } from "@/lib/nav";

/**
 * Desktop navigation: every built page, grouped as in the brief, from the
 * same registry as the phone dock (src/lib/nav.ts). The current page gets
 * accent text and a short accent bar on the rail line.
 */
export function RailNav() {
  const pathname = usePathname();

  return (
    <nav aria-label="Pages" className="flex flex-col gap-6">
      {builtGroups().map((g) => (
        <div key={g.key}>
          <h2 className="label-mono px-3 text-text-muted">{g.name}</h2>
          <ul className="mt-2 ml-3 border-l border-border pl-2">
            {g.pages.map((p) => {
              const active = isActive(pathname, p.href);
              return (
                <li key={p.href} className="relative">
                  {active && (
                    <span aria-hidden className="absolute top-2 -left-2.25 h-5 w-0.5 rounded-pill bg-accent" />
                  )}
                  <Link
                    href={p.href}
                    aria-current={active ? "page" : undefined}
                    className={`flex h-9 items-center rounded-inner px-2.5 text-sm transition-colors hover:bg-surface-2 ${
                      active ? "bg-surface-2 font-medium text-accent" : "text-text-muted hover:text-text"
                    }`}
                  >
                    {p.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
