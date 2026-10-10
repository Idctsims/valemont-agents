// The Morning Brief as the web reads it (db/030 briefs, kind 'morning').
// Pure: shared by server pages, client components and the unit specs.

export type BriefSection = { key: string; title: string; lines: string[] };

export type BriefBody = { headline: string; sections: BriefSection[]; one_move: string };

export type Brief = {
  id: number;
  date: string; // YYYY-MM-DD, the owner's local date
  createdAt: string;
  body: BriefBody;
  /** The deterministic template, built when the AI path failed. */
  fallback: boolean;
  model: string | null;
  wordCount: number;
  /** An e2e test brief; only a verified marker request ever sees one. */
  isTest: boolean;
};

export const OWNER_TZ = "America/Chicago";

/** "6:15 AM": when the brief was written, in the owner's time. */
export function briefTime(iso: string, tz = OWNER_TZ): string {
  return new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: tz }).format(new Date(iso));
}

/** "Sat, Oct 10" for a YYYY-MM-DD date. */
export function briefDay(day: string): string {
  return new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" }).format(
    new Date(`${day}T00:00:00Z`),
  );
}

/** A brief written after the 09:45 push time (a boot catch-up or a late retry). */
export function isLate(iso: string, tz = OWNER_TZ): boolean {
  const [h, m] = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: tz })
    .format(new Date(iso))
    .split(":")
    .map(Number);
  return h * 60 + m > 9 * 60 + 45;
}

/** The latest brief per date, newest date first (the history list). */
export function latestPerDate(rows: Brief[]): Brief[] {
  const byDate = new Map<string, Brief>();
  for (const b of rows) {
    const seen = byDate.get(b.date);
    if (!seen || b.id > seen.id) byDate.set(b.date, b);
  }
  return [...byDate.values()].sort((a, b) => (a.date < b.date ? 1 : -1));
}
