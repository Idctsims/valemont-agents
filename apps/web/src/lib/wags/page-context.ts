// Where Wags was opened from: sent with each message while the sheet's
// context chip is present, stored on the user message (page_context), and
// used to pull that page's slice out of the context snapshot. Shared by the
// client (which builds it) and the route (which validates it).

export const PILLARS = ["goals", "ventures", "capital"] as const;
export type Pillar = (typeof PILLARS)[number];

export type PageContext = {
  /** Same-origin path, e.g. "/ventures/sail-beach-club". */
  route: string;
  /** What the chip says after "Context ·", e.g. "Ventures · Sail Beach Club". */
  label: string;
  pillar?: Pillar;
  /** A venture's slug, on a venture page. */
  slug?: string;
};

/** A page context from the wire, or null if any field is off. */
export function parsePageContext(v: unknown): PageContext | null {
  if (!v || typeof v !== "object") return null;
  const o = v as Record<string, unknown>;
  const route = typeof o.route === "string" && /^\/(?!\/)[\w\-/]{0,199}$/.test(o.route) ? o.route : null;
  const label = typeof o.label === "string" && o.label.trim() && o.label.length <= 120 ? o.label.trim() : null;
  if (!route || !label) return null;
  const pillar = PILLARS.includes(o.pillar as Pillar) ? (o.pillar as Pillar) : undefined;
  const slug = typeof o.slug === "string" && /^[a-z0-9]+(-[a-z0-9]+)*$/.test(o.slug) && o.slug.length <= 80 ? o.slug : undefined;
  return { route, label, ...(pillar ? { pillar } : {}), ...(slug ? { slug } : {}) };
}

/** That page's part of the snapshot: one venture on a venture page, else the pillar. */
export function pageSlice(payload: Record<string, unknown>, page: PageContext): unknown {
  if (!page.pillar) return null;
  const pillar = payload[page.pillar];
  if (page.pillar === "ventures" && page.slug && pillar && typeof pillar === "object") {
    const active = (pillar as { active?: { slug?: string }[] }).active ?? [];
    return active.find((v) => v.slug === page.slug) ?? { slug: page.slug, note: "Not set up, or archived: no detail in the context." };
  }
  return pillar ?? null;
}
