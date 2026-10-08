import Link from "next/link";

import type { Theme } from "@/lib/theme";

import { AccountMenu } from "./account-menu";
import { LogoutButton } from "./logout-button";
import { RailNav } from "./rail-nav";
import { TabBar } from "./tab-bar";
import { ThemeToggle } from "./theme-toggle";

/**
 * Phone: a slim top bar (wordmark, theme, account menu holding log out) and a
 * bottom tab bar under the thumb. Desktop (lg+): a fixed left rail with every
 * pillar, theme and log out.
 */
export function AppShell({ theme, children }: { theme: Theme; children: React.ReactNode }) {
  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:rounded-pill focus:bg-accent focus:px-4 focus:py-2 focus:text-on-accent"
      >
        Skip to content
      </a>

      <aside className="fixed inset-y-0 left-0 z-40 hidden w-rail flex-col border-r border-border bg-surface lg:flex">
        <div className="px-6 pt-7 pb-5">
          <Link href="/" className="font-display text-3xl text-text">
            Valemont
          </Link>
          <p className="label-mono mt-1 text-text-muted">Command</p>
        </div>
        <div className="flex-1 overflow-y-auto px-3 pb-6">
          <RailNav />
        </div>
        <div className="flex items-center justify-between border-t border-border px-3 py-3">
          <ThemeToggle initial={theme} withLabel />
          <LogoutButton />
        </div>
      </aside>

      <header className="sticky top-0 z-30 border-b border-border bg-bg pt-safe lg:hidden">
        <div className="flex h-14 items-center justify-between px-safe">
          <Link href="/" className="font-display text-2xl text-text">
            Valemont
          </Link>
          <div className="-mr-2 flex items-center">
            <ThemeToggle initial={theme} />
            <AccountMenu>
              <LogoutButton menuItem />
            </AccountMenu>
          </div>
        </div>
      </header>

      <main id="main" className="pb-tabbar lg:pb-20 lg:pl-rail">
        <div className="mx-auto max-w-shell px-safe pt-8 lg:px-12 lg:pt-16">{children}</div>
      </main>

      <TabBar />
    </>
  );
}
