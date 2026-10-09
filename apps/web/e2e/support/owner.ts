import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, type Page } from "@playwright/test";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

// Shared by the owner-only specs. Credentials come from the shell session
// only, never a file:
//   $env:E2E_OWNER_EMAIL = '...'; $env:E2E_OWNER_PASSWORD = '...'
export const email = process.env.E2E_OWNER_EMAIL;
export const password = process.env.E2E_OWNER_PASSWORD;
export const hasOwner = !!email && !!password;

/** Every goal an owner spec creates starts with this, so cleanup finds it. */
export const E2E_PREFIX = "e2e ";

export async function signIn(page: Page, next = "/goals") {
  await page.goto(`/login?next=${encodeURIComponent(next)}`);
  await page.getByLabel("Email").fill(email!);
  await page.getByLabel("Password").fill(password!);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL((url) => url.pathname + url.search === next);
}

export async function setTheme(page: Page, theme: "night" | "day") {
  // Clear first, then set at path "/" on the host. Added with `url:` from a
  // nested page, the cookie took that page's directory as its path
  // (/ventures), the earlier "/" one survived, the browser sent both, and
  // the server read "night" (2026-10-09 trace). The app's own toggle always
  // writes path=/.
  const context = page.context();
  await context.clearCookies({ name: "vm-theme" });
  await context.addCookies([
    { name: "vm-theme", value: theme, domain: new URL(page.url()).hostname, path: "/" },
  ]);
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
}

/** The two public values from .env.local; nothing secret is read. */
function publicEnv(): { url: string; key: string } {
  const text = readFileSync(resolve(__dirname, "..", "..", ".env.local"), "utf8");
  const get = (name: string) => text.match(new RegExp(`^${name}=(.*)$`, "m"))?.[1]?.trim() ?? "";
  return { url: get("NEXT_PUBLIC_SUPABASE_URL"), key: get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY") };
}

/** Seeded goals read like real ones on screen; this note is what marks them. */
export const SEED_NOTE = "e2e-seed";

/**
 * Supabase's API gateway can stamp a fresh token a moment ahead of
 * PostgREST's clock, which then refuses it: "JWT issued at future"
 * (PGRST303, CLAUDE.md; the watchdog route retries for it too). It hit the
 * first query after sign-in on 2026-10-09. Probe with a harmless read until
 * the token is accepted, for up to 5 s, BEFORE any real query or write, so
 * nothing that writes is ever retried.
 */
async function untilTokenAccepted(supabase: SupabaseClient) {
  for (let i = 0; i < 10; i++) {
    const { error } = await supabase.from("app_settings").select("timezone").limit(1);
    if (!error) return;
    if (!/issued at future/i.test(error.message)) throw new Error(`owner session check failed: ${error.message}`);
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("owner session: still 'JWT issued at future' after 5 s (Supabase clock skew)");
}

/** Run `fn` with a supabase-js client signed in as the owner (RLS applies). */
async function asOwner<T>(fn: (supabase: SupabaseClient) => Promise<T>): Promise<T> {
  const { url, key } = publicEnv();
  const supabase = createClient(url, key, { auth: { persistSession: false } });
  const { error } = await supabase.auth.signInWithPassword({ email: email!, password: password! });
  if (error) throw new Error(`owner sign-in failed: ${error.message}`);
  try {
    await untilTokenAccepted(supabase);
    return await fn(supabase);
  } finally {
    // scope 'local' ONLY. supabase-js defaults to 'global', which revokes
    // every session the owner has: the other test browsers still running,
    // and the owner's real phone. That failed the phone project on 2026-10-09.
    await supabase.auth.signOut({ scope: "local" });
  }
}

/**
 * One id per worker process. The desktop and phone projects run in separate
 * workers at the same time, so each spec run titles its goals with this and
 * deletes only those: on 2026-10-09 a shared "delete every e2e goal" cleanup,
 * run by the desktop worker as it finished, deleted the goal the phone CRUD
 * test was still editing.
 */
export const RUN_ID = `${Date.now().toString(36)}${process.pid}`;

/** Title prefix for this worker's goals: "e2e <run> ". */
export const runPrefix = () => `${E2E_PREFIX}${RUN_ID} `;

/**
 * Delete exactly these goals, as the owner (RLS applies), and nothing else.
 * The owner's real goals live in the same account and the same week, so a
 * spec cleans up by the ids it created, never by a pattern. Leaves first: a
 * goal cannot be deleted while its carried row exists (db/021).
 */
async function deleteGoalsById(supabase: SupabaseClient, ids: string[]) {
  let left = [...new Set(ids)];
  for (let pass = 0; pass < 10 && left.length; pass++) {
    const { data, error } = await supabase.from("goals").select("id, carried_from").in("id", left);
    if (error) throw new Error(`cleanup read failed: ${error.message}`);
    if (!data.length) return;
    const parents = new Set(data.map((g) => g.carried_from).filter(Boolean));
    const leaves = data.filter((g) => !parents.has(g.id)).map((g) => g.id);
    const del = await supabase.from("goals").delete().in("id", leaves);
    if (del.error) throw new Error(`cleanup failed: ${del.error.message}`);
    left = data.filter((g) => parents.has(g.id)).map((g) => g.id);
  }
  if (left.length) throw new Error(`cleanup: ${left.length} created goal(s) still present after 10 passes`);
}

/**
 * Delete the goals this worker's specs made: titled with `titlePrefix`
 * (runPrefix(), unique per worker process), so the ids come from a title only
 * this run could have written. Never another run's, never the owner's.
 */
export async function deleteE2eGoals(scope: { titlePrefix: string }) {
  await asOwner(async (supabase) => {
    const { data, error } = await supabase
      .from("goals")
      .select("id")
      .like("title", `${scope.titlePrefix}%`);
    if (error) throw new Error(`cleanup read failed: ${error.message}`);
    await deleteGoalsById(supabase, data.map((g) => g.id));
  });
}

/**
 * Every goal the owner has, as id -> its fields. Taken before and after a
 * spec: each goal present before must be present and unchanged after. New
 * rows are allowed only as ensureRollover's carries of the owner's own goals.
 */
export async function snapshotGoals(): Promise<Map<string, string>> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase
      .from("goals")
      .select("id, title, notes, horizon, area, period_start, status, carried_from, carry_count, completed_at");
    if (error) throw new Error(`snapshot failed: ${error.message}`);
    // The owner's goals only: rows another e2e run is writing at the same
    // moment (parallel projects) are that run's business, not the owner's.
    const real = data.filter((g) => !String(g.title).startsWith(E2E_PREFIX) && g.notes !== SEED_NOTE);
    return new Map(real.map((g) => [g.id as string, JSON.stringify(g)]));
  });
}

/** Delete exactly the goals in `ids` (what seedScreens recorded). */
export async function deleteSeededGoals(ids: string[]) {
  if (!ids.length) return;
  await asOwner((supabase) => deleteGoalsById(supabase, ids));
}

/**
 * What the seed adds to the current week. Slots: 8 open (one carried in
 * twice) + 2 done. Folded under "Moved on": 1 dropped + 1 moved to next week.
 * Kept beside the seed list so the two cannot drift apart unnoticed.
 */
export const SEEDED_WEEK = { open: 8, done: 2, folded: 2 } as const;

type Seed = {
  title: string;
  horizon: "weekly" | "monthly" | "long_term";
  period: string | null;
  area?: string;
  status?: "open" | "done" | "dropped";
  /** Carry it this many times after inserting (carry_goal, db/021). */
  carries?: number;
};

/**
 * A realistic week for the screenshot review: open, done, carried twice,
 * dropped, moved on; a past week for History; month and long-term goals.
 *
 * Every id it creates (inserts and carried copies) is pushed into `created`
 * as it goes, so the caller can delete exactly those, even after a failure
 * halfway. Its effect on THIS week is SEEDED_WEEK, for relative assertions.
 */
export async function seedScreens(
  created: string[],
  thisWeek: string,
  lastWeek: string,
  twoWeeksAgo: string,
  month: string,
) {
  const seeds: Seed[] = [
    { title: "Send Clipd the revised term sheet", horizon: "weekly", period: thisWeek, area: "business" },
    { title: "Draft the Sail Beach Club budget", horizon: "weekly", period: thisWeek, area: "business" },
    { title: "Four lifts this week", horizon: "weekly", period: thisWeek, area: "health", status: "done" },
    { title: "Book the Excursion site visit", horizon: "weekly", period: thisWeek, status: "done" },
    { title: "Review the Excursion vendor contract", horizon: "weekly", period: thisWeek, area: "business" },
    { title: "Dinner with Dad on Thursday", horizon: "weekly", period: thisWeek, area: "people" },
    { title: "Meal prep for the week", horizon: "weekly", period: thisWeek, area: "health" },
    { title: "Move $2k into the capital account", horizon: "weekly", period: thisWeek, area: "money" },
    { title: "Reply to the Clipd designer", horizon: "weekly", period: thisWeek, area: "business" },
    { title: "Close out the Q3 books", horizon: "weekly", period: twoWeeksAgo, area: "money", carries: 2 },
    { title: "Reorganise the garage", horizon: "weekly", period: thisWeek, area: "personal", status: "dropped" },
    { title: "Call Marcus about the lease", horizon: "weekly", period: thisWeek, area: "people", carries: 1 },
    { title: "Send the Perfect Timing invoice", horizon: "weekly", period: lastWeek, area: "money", status: "done" },
    { title: "Run the Valemont Grow standup", horizon: "weekly", period: lastWeek, area: "business", status: "done" },
    { title: "Sell the old monitor", horizon: "weekly", period: lastWeek, area: "personal", status: "dropped" },
    { title: "Close two freelance web dev leads", horizon: "monthly", period: month, area: "business" },
    { title: "Ten workouts", horizon: "monthly", period: month, area: "health", status: "done" },
    { title: "First outside LP for Sims & Vale Capital", horizon: "long_term", period: null, area: "money" },
    { title: "Run a half marathon", horizon: "long_term", period: null, area: "health" },
  ];
  await asOwner(async (supabase) => {
    for (const s of seeds) {
      const status = s.status ?? "open";
      const { data, error } = await supabase
        .from("goals")
        .insert({
          title: s.title,
          horizon: s.horizon,
          period_start: s.period,
          area: s.area ?? null,
          status,
          completed_at: status === "done" ? new Date().toISOString() : null,
          notes: SEED_NOTE,
        })
        .select("id")
        .single();
      if (error) throw new Error(`seed failed: ${error.message}`);
      let id: string = data.id;
      created.push(id);
      for (let i = 0; i < (s.carries ?? 0); i++) {
        const carried = await supabase.rpc("carry_goal", { p_goal: id });
        if (carried.error) throw new Error(`seed carry failed: ${carried.error.message}`);
        const child = await supabase.from("goals").select("id").eq("carried_from", id).single();
        if (child.error) throw new Error(`seed carry read failed: ${child.error.message}`);
        id = child.data.id;
        created.push(id);
      }
    }
  });
}

// ---------------------------------------------------------------- ventures

/**
 * A temporary venture for one spec run: "e2e <run id> …", slug
 * "e2e-<run id>-<tag>". Created and deleted by id; deleting it cascades its
 * workstreams, dates and log (db/022 permits exactly that cascade).
 */
export async function createTempVenture(
  tag: string,
  fields: Record<string, unknown> = {},
): Promise<{ id: string; slug: string; name: string }> {
  const slug = `e2e-${RUN_ID}-${tag}`.toLowerCase();
  const name = `${runPrefix()}${tag}`;
  return asOwner(async (supabase) => {
    const { data, error } = await supabase
      .from("ventures")
      .insert({ name, slug, sort_order: 9999, ...fields })
      .select("id")
      .single();
    if (error) throw new Error(`temp venture failed: ${error.message}`);
    return { id: data.id as string, slug, name };
  });
}

/** Add an open date to a venture, due on `dueOn` (YYYY-MM-DD). */
export async function addTempDate(ventureId: string, label: string, dueOn: string, workstreamId?: string) {
  await asOwner(async (supabase) => {
    const { error } = await supabase
      .from("venture_dates")
      .insert({ venture_id: ventureId, label, due_on: dueOn, workstream_id: workstreamId ?? null });
    if (error) throw new Error(`temp date failed: ${error.message}`);
  });
}

/** Delete exactly these ventures (and, by cascade, everything under them). */
export async function deleteVenturesById(ids: string[]) {
  if (!ids.length) return;
  await asOwner(async (supabase) => {
    const { error } = await supabase.from("ventures").delete().in("id", ids);
    if (error) throw new Error(`venture cleanup failed: ${error.message}`);
  });
}

/**
 * The owner's real ventures and everything under them, as one string per
 * row keyed by table and id. Temporary "e2e …" ventures are left out. Taken
 * before and after a spec: every entry must be identical afterwards.
 */
export async function snapshotVentures(): Promise<Map<string, string>> {
  return asOwner(async (supabase) => {
    const [v, w, d, l] = await Promise.all([
      supabase.from("ventures").select("*"),
      supabase.from("venture_workstreams").select("*"),
      supabase.from("venture_dates").select("*"),
      supabase.from("venture_log").select("*"),
    ]);
    for (const r of [v, w, d, l]) if (r.error) throw new Error(`venture snapshot failed: ${r.error.message}`);
    const real = new Set(
      v.data!.filter((x) => !String(x.name).startsWith(E2E_PREFIX)).map((x) => x.id as string),
    );
    const out = new Map<string, string>();
    for (const x of v.data!) if (real.has(x.id)) out.set(`ventures:${x.id}`, JSON.stringify(x));
    for (const [table, rows] of [
      ["venture_workstreams", w.data!],
      ["venture_dates", d.data!],
      ["venture_log", l.data!],
    ] as const) {
      for (const x of rows) if (real.has(x.venture_id)) out.set(`${table}:${x.id}`, JSON.stringify(x));
    }
    return out;
  });
}
