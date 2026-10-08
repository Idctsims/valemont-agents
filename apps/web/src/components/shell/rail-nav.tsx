"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { SECTIONS, pad2 } from "@/lib/sitemap";

import { SectionIcon } from "./section-icon";

/**
 * Desktop navigation: every section with its pillars, always visible.
 * The current page gets accent text and a short accent bar on the rail line.
 */
export function RailNav() {
  const pathname = usePathname();

  return (
    <nav aria-label="Pillars" className="flex flex-col gap-3">
      {SECTIONS.map((s) => {
        const sectionHref = `/${s.key}`;
        const onSection = pathname === sectionHref;
        const inSection = onSection || pathname.startsWith(`${sectionHref}/`);
        return (
          <div key={s.key}>
            <Link
              href={sectionHref}
              aria-current={onSection ? "page" : undefined}
              className={`relative flex h-9 items-center gap-3 rounded-inner px-3 transition-colors hover:bg-surface-2 ${
                onSection
                  ? "bg-surface-2 text-accent"
                  : inSection
                    ? "text-text"
                    : "text-text-muted hover:text-text"
              }`}
            >
              {onSection && (
                <span aria-hidden className="absolute left-0 h-5 w-0.5 rounded-pill bg-accent" />
              )}
              <SectionIcon section={s.key} size={20} weight={inSection ? "fill" : "regular"} />
              <span className="text-sm font-medium">{s.name}</span>
            </Link>
            <ul className="mt-0.5 ml-5 border-l border-border pl-2">
              {s.pillars.map((p) => {
                const href = `${sectionHref}/${p.slug}`;
                const active = pathname === href;
                return (
                  <li key={p.slug} className="relative">
                    {active && (
                      <span
                        aria-hidden
                        className="absolute top-1 -left-2.25 h-5 w-0.5 rounded-pill bg-accent"
                      />
                    )}
                    <Link
                      href={href}
                      aria-current={active ? "page" : undefined}
                      className={`flex h-7 items-center gap-2.5 rounded-inner px-2.5 text-sm transition-colors hover:bg-surface-2 ${
                        active ? "bg-surface-2 font-medium text-accent" : "text-text-muted hover:text-text"
                      }`}
                    >
                      <span className="label-mono">{pad2(p.n)}</span>
                      <span className="truncate">{p.name}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </nav>
  );
}
