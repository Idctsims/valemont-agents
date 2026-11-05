// How "national" a broadcast is (owner, 2026-11-05): 1 a broadcast network,
// 2 national cable, 3 anything else (streaming-only, regional), 4 nothing
// listed. The database twin is db/068 sports_tv_tier(); workers/tests/
// test_gameday.py fails if the two lists differ. Names as ESPN stores them,
// compared upper-case. Pure: safe anywhere.

export const BROADCAST_NETWORKS = ["ABC", "CBS", "NBC", "FOX"] as const;

export const NATIONAL_CABLE = [
  "ESPN", "ESPN2", "ESPNU", "FS1", "SEC NETWORK", "BTN", "ACC NETWORK", "CBSSN", "CW", "USA NET", "TNT", "TRUTV",
  "NFL NET", "NBA TV", "GOLF CHNL",
] as const;

export type TvTier = 1 | 2 | 3 | 4;

export function tvTier(broadcast: readonly string[] | null | undefined): TvTier {
  const names = (broadcast ?? []).map((b) => b.trim().toUpperCase());
  if (names.length === 0) return 4;
  if (names.some((n) => (BROADCAST_NETWORKS as readonly string[]).includes(n))) return 1;
  if (names.some((n) => (NATIONAL_CABLE as readonly string[]).includes(n))) return 2;
  return 3;
}
