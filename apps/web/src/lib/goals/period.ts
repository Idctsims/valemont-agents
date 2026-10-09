// Goal periods as plain 'YYYY-MM-DD' strings in the owner's timezone, the
// same rules as db/021: a week starts on Monday, a month on the 1st. Pure,
// so the server pages, the client ledger and the unit specs share it.

/** Same zone as app_settings.timezone, which the SQL uses for "ended". */
export const OWNER_TZ = "America/Chicago";

export const WEEKLY_CAP = 10;

export type Horizon = "weekly" | "monthly" | "long_term";
export const AREAS = ["business", "personal", "health", "money", "people"] as const;
export type Area = (typeof AREAS)[number];

/** Today's date in `tz`, as 'YYYY-MM-DD'. */
export function localToday(now: Date = new Date(), tz: string = OWNER_TZ): string {
  // en-CA formats as YYYY-MM-DD.
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: tz,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

function toUtc(day: string): Date {
  return new Date(`${day}T00:00:00Z`);
}

function fromUtc(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export function addDays(day: string, n: number): string {
  const d = toUtc(day);
  d.setUTCDate(d.getUTCDate() + n);
  return fromUtc(d);
}

/** Monday of the week holding `day`. */
export function weekStart(day: string): string {
  const dow = toUtc(day).getUTCDay(); // 0 = Sunday
  return addDays(day, -((dow + 6) % 7));
}

export function monthStart(day: string): string {
  return `${day.slice(0, 7)}-01`;
}

export function periodFor(horizon: Horizon, today: string): string | null {
  if (horizon === "weekly") return weekStart(today);
  if (horizon === "monthly") return monthStart(today);
  return null;
}

/** ISO 8601 week: the year that owns the week's Thursday, and its number. */
export function isoWeek(day: string): { year: number; week: number } {
  const thursday = toUtc(addDays(weekStart(day), 3));
  const year = thursday.getUTCFullYear();
  const jan1 = Date.UTC(year, 0, 1);
  const week = Math.floor((thursday.getTime() - jan1) / 86_400_000 / 7) + 1;
  return { year, week };
}

/** '2026-W41' */
export function isoWeekLabel(day: string): string {
  const { year, week } = isoWeek(day);
  return `${year}-W${String(week).padStart(2, "0")}`;
}

/** 'Week 41 · Q4': the ISO week, and the calendar quarter of `day`. */
export function weekStamp(day: string): string {
  const quarter = Math.floor((Number(day.slice(5, 7)) - 1) / 3) + 1;
  return `Week ${isoWeek(day).week} · Q${quarter}`;
}

/** 'Friday, October 9' */
export function longDate(day: string): string {
  return new Intl.DateTimeFormat("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  }).format(toUtc(day));
}

/** 'Oct 5 – 11' or 'Sep 28 – Oct 4' for the week starting `monday`. */
export function weekRange(monday: string): string {
  const fmt = (d: string, withMonth: boolean) =>
    new Intl.DateTimeFormat("en-US", {
      month: withMonth ? "short" : undefined,
      day: "numeric",
      timeZone: "UTC",
    }).format(toUtc(d));
  const sunday = addDays(monday, 6);
  return `${fmt(monday, true)} – ${fmt(sunday, sunday.slice(5, 7) !== monday.slice(5, 7))}`;
}

/** 'October 2026' */
export function monthName(first: string): string {
  return new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" }).format(
    toUtc(first),
  );
}
