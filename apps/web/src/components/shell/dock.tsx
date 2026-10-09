"use client";

import { DotsThreeCircle, House, Target, type IconProps } from "@phosphor-icons/react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";

import { SYSTEM_PAGES, builtGroups, isActive } from "@/lib/nav";

// Phone navigation, in the thumb zone: Home and Goals on the left, a centre
// slot held for Wags (Chat 2 Phase 4, renders nothing yet), More on the
// right. The two halves are equal width, so the centre slot is truly centred
// whatever each side holds. More opens a sheet of every built page, from the
// same registry as the desktop rail (src/lib/nav.ts).

const DOCK: { label: string; href: string; Icon: React.ComponentType<IconProps> }[] = [
  { label: "Home", href: "/", Icon: House },
  { label: "Goals", href: "/goals", Icon: Target },
];

const item =
  "tap relative flex flex-1 flex-col items-center justify-center gap-1 rounded-inner text-xs transition-colors";

export function Dock() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const sheetId = useId();
  const moreButton = useRef<HTMLButtonElement>(null);

  // Close on navigation.
  const [seen, setSeen] = useState(pathname);
  if (seen !== pathname) {
    setSeen(pathname);
    setOpen(false);
  }

  const inDock = DOCK.some((d) => isActive(pathname, d.href));

  return (
    <>
      {open && (
        <MoreSheet
          id={sheetId}
          pathname={pathname}
          onClose={() => {
            setOpen(false);
            moreButton.current?.focus();
          }}
        />
      )}
      <nav
        aria-label="Dock"
        className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-surface pb-safe lg:hidden"
      >
        <div className="mx-auto flex h-tabbar max-w-xl items-stretch px-2">
          <ul className="flex flex-1">
            {DOCK.map(({ label, href, Icon }) => {
              const active = isActive(pathname, href);
              return (
                <li key={href} className="flex flex-1">
                  <Link
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className={`${item} ${active ? "font-medium text-accent" : "text-text-muted hover:text-text"}`}
                  >
                    <span
                      aria-hidden
                      className={`absolute -top-px h-0.5 w-8 transition-colors ${active ? "bg-accent" : "bg-transparent"}`}
                    />
                    <Icon size={24} weight={active ? "fill" : "regular"} aria-hidden />
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>

          {/* Reserved for Wags. Holds its width; renders nothing until then. */}
          <div data-slot="wags" aria-hidden className="w-dock-slot shrink-0" />

          <div className="flex flex-1">
            <button
              ref={moreButton}
              type="button"
              aria-expanded={open}
              aria-controls={sheetId}
              onClick={() => setOpen((o) => !o)}
              className={`${item} ${open || !inDock ? "text-accent" : "text-text-muted hover:text-text"}`}
            >
              <DotsThreeCircle size={24} weight={open ? "fill" : "regular"} aria-hidden />
              More
            </button>
          </div>
        </div>
      </nav>
    </>
  );
}

function MoreSheet({ id, pathname, onClose }: { id: string; pathname: string; onClose: () => void }) {
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    panel.current?.querySelector<HTMLElement>("a")?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const groups = [...builtGroups(), { key: "system", name: "System", pages: SYSTEM_PAGES }];

  return (
    <div className="fixed inset-0 z-40 lg:hidden">
      <button
        type="button"
        aria-label="Close"
        tabIndex={-1}
        onClick={onClose}
        className="absolute inset-0 h-full w-full bg-bg/70"
      />
      <div
        ref={panel}
        id={id}
        role="dialog"
        aria-modal="true"
        aria-label="All pages"
        className="absolute inset-x-0 bottom-dock rounded-t-card border-t border-border bg-surface px-safe pt-6 pb-4"
      >
        {groups.map((g) => (
          <section key={g.key} aria-label={g.name} className="mb-4 last:mb-0">
            <h2 className="label-mono text-text-muted">{g.name}</h2>
            <ul className="mt-2 border-t border-border">
              {g.pages.map((p) => {
                const active = isActive(pathname, p.href);
                return (
                  <li key={p.href} className="border-b border-border">
                    <Link
                      href={p.href}
                      aria-current={active ? "page" : undefined}
                      onClick={onClose}
                      className={`tap flex items-center text-base transition-colors ${
                        active ? "font-medium text-accent" : "text-text hover:text-accent"
                      }`}
                    >
                      {p.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}
