"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { SECTIONS, pad2 } from "@/lib/sitemap";

import { SectionIcon } from "./section-icon";

/** Desktop navigation: every section with its pillars, always visible. */
export function RailNav() {
  const pathname = usePathname();

  return (
    <nav aria-label="Pillars" className="flex flex-col gap-3">
      {SECTIONS.map((s) => {
        const sectionHref = `/${s.key}`;
        const inSection = pathname === sectionHref || pathname.startsWith(`${sectionHref}/`);
        return (
          <div key={s.key}>
            <Link
              href={sectionHref}
              aria-current={pathname === sectionHref ? "page" : undefined}
              className={`flex h-9 items-center gap-3 rounded-inner px-3 transition-colors hover:bg-surface-2 ${
                inSection ? "text-text" : "text-text-muted hover:text-text"
              }`}
            >
              <SectionIcon section={s.key} size={20} weight={inSection ? "fill" : "regular"} />
              <span className="text-sm font-medium">{s.name}</span>
            </Link>
            <ul className="mt-0.5 ml-5 border-l border-border pl-2">
              {s.pillars.map((p) => {
                const href = `${sectionHref}/${p.slug}`;
                const active = pathname === href;
                return (
                  <li key={p.slug}>
                    <Link
                      href={href}
                      aria-current={active ? "page" : undefined}
                      className={`flex h-7 items-center gap-2.5 rounded-inner px-2.5 text-sm transition-colors hover:bg-surface-2 ${
                        active ? "bg-surface-2 text-text" : "text-text-muted hover:text-text"
                      }`}
                    >
                      <span className={`label-mono ${active ? "text-accent" : ""}`}>{pad2(p.n)}</span>
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
