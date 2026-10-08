"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { SECTIONS } from "@/lib/sitemap";

import { SectionIcon } from "./section-icon";

/**
 * Phone navigation: the five sections, under the thumb. The current section
 * gets accent text, a filled icon and an accent bar along the top edge.
 */
export function TabBar() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Sections"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-surface pb-safe lg:hidden"
    >
      <ul className="mx-auto grid h-tabbar max-w-xl grid-cols-5 px-1">
        {SECTIONS.map((s) => {
          const active = pathname === `/${s.key}` || pathname.startsWith(`/${s.key}/`);
          return (
            <li key={s.key} className="flex">
              <Link
                href={`/${s.key}`}
                aria-current={active ? "page" : undefined}
                className={`tap relative flex flex-1 flex-col items-center justify-center gap-1 rounded-inner transition-colors ${
                  active ? "text-accent" : "text-text-muted hover:text-text"
                }`}
              >
                <span
                  aria-hidden
                  className={`absolute -top-px h-1 w-8 rounded-pill transition-colors ${
                    active ? "bg-accent" : "bg-transparent"
                  }`}
                />
                <SectionIcon section={s.key} size={24} weight={active ? "fill" : "regular"} />
                <span className={`label-mono ${active ? "font-semibold" : ""}`}>{s.name}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
