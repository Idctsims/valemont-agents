import { DeviceMobile, Heartbeat, Palette } from "@phosphor-icons/react/ssr";
import Link from "next/link";

import { AskWagsButton } from "@/components/wags/ask-wags-button";
import { WagsProvider } from "@/components/wags/wags-provider";
import type { Theme } from "@/lib/theme";

import { AccountMenu } from "./account-menu";
import { LogoutButton } from "./logout-button";
import { RailNav } from "./rail-nav";
import { Dock } from "./dock";
import { ThemeToggle } from "./theme-toggle";

/**
 * Phone: a slim top bar (wordmark, theme, account menu holding log out) and a
 * bottom tab bar under the thumb. Desktop (lg+): a fixed left rail with every
 * pillar, theme and log out.
 */
const SYSTEM_LINKS = [
  { href: "/settings/health", label: "System health", Icon: Heartbeat },
  { href: "/onboarding", label: "Phone setup", Icon: DeviceMobile },
  { href: "/design", label: "Design tokens", Icon: Palette },
];

export function AppShell({ theme, children }: { theme: Theme; children: React.ReactNode }) {
  return (
    <WagsProvider>
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
        <div className="px-3 pb-5">
          <AskWagsButton />
        </div>
        <div className="flex-1 overflow-y-auto px-3 pb-6">
          <RailNav />
        </div>
        <div className="border-t border-border px-3 py-3">
          {SYSTEM_LINKS.map(({ href, label, Icon }) => (
            <Link
              key={href}
              href={href}
              className="tap flex items-center gap-2 rounded-pill px-3 text-sm text-text-muted transition-colors hover:bg-surface-2 hover:text-text"
            >
              <Icon size={20} aria-hidden />
              {label}
            </Link>
          ))}
          <div className="flex items-center justify-between">
            <ThemeToggle initial={theme} withLabel />
            <LogoutButton />
          </div>
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
              <Link
                href="/onboarding"
                className="tap flex w-full items-center gap-3 rounded-inner px-3 text-sm text-text transition-colors hover:bg-surface-2"
              >
                <DeviceMobile size={20} aria-hidden />
                Set up this phone
              </Link>
              <LogoutButton menuItem />
            </AccountMenu>
          </div>
        </div>
      </header>

      <main id="main" className="pb-tabbar lg:pb-20 lg:pl-rail">
        <div className="mx-auto max-w-shell px-safe pt-8 lg:px-12 lg:pt-16">{children}</div>
      </main>

      <Dock />
    </WagsProvider>
  );
}
