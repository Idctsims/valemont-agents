// Navigation registry: the sixteen pillars of docs/BUILD_BRIEF.md, in the
// brief's five groups, each marked built or not. The dock, the More sheet and
// the desktop rail all read this one list, and only built pages render: a
// pillar flips to `built: true` in the chat that ships it, and appears.

export type GroupKey = "command" | "culture" | "tech" | "sports" | "crypto";

export type NavPage = {
  label: string;
  href: string;
};

export type Pillar = {
  /** Position in the brief's site map, 1–16. */
  n: number;
  name: string;
  built: boolean;
  /** Where the pillar lives once built. */
  href: string;
  /** Pages inside the pillar that have their own route (Home holds Goals). */
  pages?: NavPage[];
};

export type NavGroup = {
  key: GroupKey;
  name: string;
  pillars: Pillar[];
};

export const NAV: NavGroup[] = [
  {
    key: "command",
    name: "Command",
    pillars: [
      { n: 1, name: "Home", built: true, href: "/", pages: [{ label: "Goals", href: "/goals" }] },
      { n: 2, name: "Wags", built: false, href: "/wags" },
      { n: 3, name: "Ventures HQ", built: false, href: "/ventures" },
      { n: 4, name: "Capital Tracker", built: false, href: "/capital" },
    ],
  },
  {
    key: "culture",
    name: "Culture",
    pillars: [
      { n: 5, name: "The Arsenal", built: false, href: "/arsenal" },
      { n: 6, name: "Screening Room", built: false, href: "/screening-room" },
      { n: 7, name: "The Lookbook", built: false, href: "/lookbook" },
    ],
  },
  {
    key: "tech",
    name: "Tech",
    pillars: [{ n: 8, name: "Tech Hub", built: false, href: "/tech-hub" }],
  },
  {
    key: "sports",
    name: "Sports",
    pillars: [
      { n: 9, name: "Sports News", built: false, href: "/sports-news" },
      { n: 10, name: "Game Day", built: false, href: "/game-day" },
      { n: 11, name: "Betting", built: false, href: "/betting" },
      { n: 12, name: "Live Bet Tracker", built: false, href: "/live-tracker" },
    ],
  },
  {
    key: "crypto",
    name: "Crypto",
    pillars: [
      { n: 13, name: "Long-Term Home", built: false, href: "/long-term" },
      { n: 14, name: "Core Paper Bot", built: false, href: "/core-bot" },
      { n: 15, name: "Memecoin Paper Bot", built: false, href: "/memecoin-bot" },
      { n: 16, name: "Kalshi 15-Min BTC", built: false, href: "/btc-15" },
    ],
  },
];

/** Pages that keep the system running. Not pillars; always built. */
export const SYSTEM_PAGES: NavPage[] = [
  { label: "System health", href: "/settings/health" },
  { label: "Phone setup", href: "/onboarding" },
  { label: "Design tokens", href: "/design" },
];

/** Every built page, grouped; groups with nothing built are left out. */
export function builtGroups(): { key: GroupKey; name: string; pages: NavPage[] }[] {
  return NAV.map((g) => ({
    key: g.key,
    name: g.name,
    pages: g.pillars
      .filter((p) => p.built)
      .flatMap((p) => [{ label: p.name, href: p.href }, ...(p.pages ?? [])]),
  })).filter((g) => g.pages.length > 0);
}

/** `/` is active only on itself; any other href also owns its subpaths. */
export function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}
