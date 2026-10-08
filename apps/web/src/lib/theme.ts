export const THEMES = ["night", "day"] as const;
export type Theme = (typeof THEMES)[number];

export const THEME_COOKIE = "vm-theme";
export const DEFAULT_THEME: Theme = "night";

export function parseTheme(value: string | undefined | null): Theme {
  return value === "day" ? "day" : DEFAULT_THEME;
}

// One year. Not httpOnly: the toggle writes it client-side so the switch is
// instant, and it holds nothing but "night" or "day".
export const THEME_COOKIE_MAX_AGE = 60 * 60 * 24 * 365;
