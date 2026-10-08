"use client";

import { Moon, Sun } from "@phosphor-icons/react";
import { useSyncExternalStore } from "react";

import { THEME_COOKIE, THEME_COOKIE_MAX_AGE, parseTheme, type Theme } from "@/lib/theme";

// The <html data-theme> attribute is the source of truth. Every toggle on the
// page (rail and top bar) subscribes to it, so they never disagree.
function subscribe(onChange: () => void) {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => observer.disconnect();
}

const read = () => parseTheme(document.documentElement.dataset.theme);

export function ThemeToggle({ initial, withLabel = false }: { initial: Theme; withLabel?: boolean }) {
  const theme = useSyncExternalStore(subscribe, read, () => initial);
  const next: Theme = theme === "night" ? "day" : "night";

  function toggle() {
    document.documentElement.dataset.theme = next;
    document.cookie = `${THEME_COOKIE}=${next}; path=/; max-age=${THEME_COOKIE_MAX_AGE}; samesite=lax`;
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${next} theme`}
      className="tap inline-flex items-center justify-center gap-2 rounded-pill px-3 text-text-muted transition-colors hover:bg-surface-2 hover:text-text"
    >
      {theme === "night" ? <Sun size={20} aria-hidden /> : <Moon size={20} aria-hidden />}
      {withLabel && <span className="text-sm">{next === "day" ? "Day" : "Night"}</span>}
    </button>
  );
}
