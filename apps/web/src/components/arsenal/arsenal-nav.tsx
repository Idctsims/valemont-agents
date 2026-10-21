import Link from "next/link";

/** The Arsenal's three pages (Chat 5 Phase 2): today with Arm Me, My
 *  Arsenal, the Monday quiz. Each link is a 44 px target. */
export function ArsenalNav({ current }: { current: "today" | "saved" | "quiz" }) {
  const links = [
    { key: "today", href: "/arsenal", label: "Today" },
    { key: "saved", href: "/arsenal/saved", label: "My Arsenal" },
    { key: "quiz", href: "/arsenal/quiz", label: "Monday quiz" },
  ] as const;
  return (
    <nav aria-label="The Arsenal" data-testid="arsenal-nav" className="-ml-3 mt-4 flex flex-wrap">
      {links.map((l) => (
        <Link key={l.key} href={l.href} aria-current={l.key === current ? "page" : undefined}
              className={`tap inline-flex items-center rounded-pill px-3 text-sm transition-colors ${
                l.key === current ? "text-accent" : "text-text-muted hover:bg-surface-2 hover:text-text"
              }`}>
          {l.label}
        </Link>
      ))}
    </nav>
  );
}
