// The owner's streaming services as TMDB US provider IDs, and the "Tonight"
// split (owner, 2026-10-14). The worker's twin is workers/screening/services.py;
// workers/tests/test_screening.py and e2e/screening-unit.spec.ts fail if the
// two differ. Ads tiers count as the owner's; the same service resold as an
// Amazon, Roku or Apple TV "channel" does not, nor Amazon Video (10), the
// rent/buy store. TMDB has no standalone US Showtime: it is in Paramount+
// Premium (2303). Pure: no token, safe anywhere.

export const MY_SERVICES: Readonly<Record<number, string>> = {
  8: "Netflix",
  1796: "Netflix",
  175: "Netflix",
  1899: "HBO Max",
  2303: "Paramount+",
  2616: "Paramount+",
  43: "Starz",
  9: "Prime Video",
  2100: "Prime Video",
  613: "Prime Video",
};

export const STREAM_KINDS = ["flatrate", "ads", "free"] as const;
export const PAY_KINDS = ["rent", "buy"] as const;
type Kind = (typeof STREAM_KINDS)[number] | (typeof PAY_KINDS)[number];

export type ProviderRow = { id: number; name: string; logo: string | null };
export type Providers = Partial<Record<Kind, ProviderRow[]>> & { link?: string };

/** TMDB's US watch/providers object as titles.providers stores it. */
export function compactProviders(us: unknown): Providers {
  const out: Providers = {};
  if (!us || typeof us !== "object") return out;
  const o = us as Record<string, unknown>;
  for (const k of [...STREAM_KINDS, ...PAY_KINDS]) {
    const rows = o[k];
    if (Array.isArray(rows) && rows.length) {
      out[k] = rows
        .filter((p): p is Record<string, unknown> => !!p && typeof p === "object" && "provider_id" in p)
        .map((p) => ({ id: Number(p.provider_id), name: String(p.provider_name ?? ""), logo: typeof p.logo_path === "string" ? p.logo_path : null }));
    }
  }
  if (typeof o.link === "string") out.link = o.link;
  return out;
}

export type TonightRow = ProviderRow & { service: string; kind: Kind };
export type OtherRow = ProviderRow & { kind: Kind };

/** Tonight: every streaming offer (flatrate, ads, free) on one of the
 * owner's services, one per service. Then the other streaming offers, then
 * rent and buy as listed. */
export function splitProviders(p: Providers | null | undefined): {
  tonight: TonightRow[]; otherStream: OtherRow[]; rent: ProviderRow[]; buy: ProviderRow[];
} {
  const tonight: TonightRow[] = [];
  const otherStream: OtherRow[] = [];
  const services = new Set<string>();
  const others = new Set<number>();
  for (const kind of STREAM_KINDS) {
    for (const row of p?.[kind] ?? []) {
      const service = MY_SERVICES[row.id];
      if (service) {
        if (!services.has(service)) {
          services.add(service);
          tonight.push({ ...row, service, kind });
        }
      } else if (!others.has(row.id)) {
        others.add(row.id);
        otherStream.push({ ...row, kind });
      }
    }
  }
  return { tonight, otherStream, rent: [...(p?.rent ?? [])], buy: [...(p?.buy ?? [])] };
}

/** A TMDB image URL (posters, provider logos). */
export function tmdbImage(path: string | null | undefined, size: "w92" | "w185" | "w342" | "w45" = "w185"): string | null {
  return path && /^\/[A-Za-z0-9_.-]+$/.test(path) ? `https://image.tmdb.org/t/p/${size}${path}` : null;
}
