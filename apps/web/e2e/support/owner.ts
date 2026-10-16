import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, type BrowserContext, type Page } from "@playwright/test";
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

/**
 * The e2e marker (db/027) from .env.local. Server-only on the app side; a
 * spec reads it to send it, and never prints it. Missing: the capital specs
 * fail at once rather than write real entries.
 */
export function e2eMarker(): string {
  const text = readFileSync(resolve(__dirname, "..", "..", ".env.local"), "utf8");
  const marker = text.match(/^E2E_TEST_MARKER=(\S+)$/m)?.[1];
  if (!marker) throw new Error("E2E_TEST_MARKER is not in apps/web/.env.local: run pnpm gen:e2e-marker");
  return marker;
}

/** The header name the server checks (src/lib/capital/marker.ts). */
export const MARKER_HEADER = "x-valemont-e2e";

/**
 * Send the marker on every request to the app itself (pages and Server
 * Actions), and on nothing else: never to Supabase or any other origin.
 */
export async function useE2eMarker(context: BrowserContext, baseURL: string) {
  const marker = e2eMarker();
  await context.route(`${baseURL}/**`, (route) =>
    route.fallback({ headers: { ...route.request().headers(), [MARKER_HEADER]: marker } }),
  );
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

/**
 * One signed-in client per worker process, made on first use and reused.
 * Signing in on every call (snapshot, create, delete...) plus each test's
 * browser sign-in tripped Supabase Auth's "Request rate limit reached" on a
 * 28-test run (2026-10-09). The session is in memory only and dies with the
 * worker; it is never signed out, because supabase-js's default sign-out is
 * 'global' and would revoke every session the owner has, phone included.
 */
let ownerClient: Promise<SupabaseClient> | null = null;

function ownerSession(): Promise<SupabaseClient> {
  ownerClient ??= (async () => {
    const { url, key } = publicEnv();
    const supabase = createClient(url, key, { auth: { persistSession: false, autoRefreshToken: true } });
    const { error } = await supabase.auth.signInWithPassword({ email: email!, password: password! });
    if (error) throw new Error(`owner sign-in failed: ${error.message}`);
    await untilTokenAccepted(supabase);
    return supabase;
  })().catch((e) => {
    ownerClient = null; // a failed sign-in is retried on next use, not cached
    throw e;
  });
  return ownerClient;
}

/** Run `fn` with the worker's client signed in as the owner (RLS applies). */
async function asOwner<T>(fn: (supabase: SupabaseClient) => Promise<T>): Promise<T> {
  return fn(await ownerSession());
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

// ----------------------------------------------------------------- capital

export type CapitalState = {
  /** The REAL paper total now, in cents (v_capital_today: test rows excluded). */
  paperTotal: number;
  /** Real paper total at the latest snapshot before today, cents; null if none. */
  paperPrior: number | null;
  /** Rows v_capital_today has for live. Today: none. */
  liveRows: number;
  /** Every REAL bankroll entry (is_test = false), id → its fields. None may change, none may be added. */
  entries: Map<string, string>;
  /** Net of every test entry (db/027), cents: what /capital adds on top for a marked e2e request. */
  testNet: number;
};

const cents = (v: string | number) => Math.round(Number(v) * 100);
const signed = (r: { kind: string; amount: string | number }) => (r.kind === "withdrawal" ? -cents(r.amount) : cents(r.amount));

/**
 * The owner's real capital, read as the owner (RLS applies), plus the test
 * entries' net. Taken before and after a spec: the real total and every real
 * entry must be exactly as they were, and no real entry may be added (a
 * spec's writes are test entries, db/027).
 */
export async function snapshotCapital(): Promise<CapitalState> {
  return asOwner(async (supabase) => {
    const [t, e] = await Promise.all([
      supabase.from("v_capital_today").select("mode, is_total, value, prior_value"),
      supabase.from("bankroll_entries").select("id, mode, kind, amount, note, created_at, owner_id, is_test"),
    ]);
    if (t.error) throw new Error(`capital read failed: ${t.error.message}`);
    if (e.error) throw new Error(`entries read failed: ${e.error.message}`);
    const total = t.data.find((r) => r.mode === "paper" && r.is_total);
    if (!total) throw new Error("no paper total: is db/026 pasted and seeded?");
    return {
      paperTotal: cents(total.value),
      paperPrior: total.prior_value === null ? null : cents(total.prior_value),
      liveRows: t.data.filter((r) => r.mode === "live").length,
      entries: new Map(e.data.filter((r) => !r.is_test).map((r) => [String(r.id), JSON.stringify(r)])),
      testNet: e.data.filter((r) => r.is_test).reduce((s, r) => s + signed(r), 0),
    };
  });
}

/**
 * Append one TEST entry (db/027: is_test, paper only, never counted) through
 * add_test_bankroll_entry, as the owner, with the marker. There is no way to
 * write a real or a live row from here.
 */
export async function addTestEntry(kind: "deposit" | "adjustment", amount: string, note: string) {
  await asOwner(async (supabase) => {
    const { error } = await supabase.rpc("add_test_bankroll_entry", {
      p_marker: e2eMarker(),
      p_kind: kind,
      p_amount: amount,
      p_note: note,
    });
    if (error) throw new Error(`test entry failed: ${error.message}`);
  });
}

/**
 * If earlier runs left the test entries netting to anything but zero (a run
 * that died between a deposit and its correction, or 2026-10-10's row 34),
 * write one TEST adjustment that brings the net back to zero, so a marked
 * page shows exactly the real total. Test rows only; the real record is
 * never touched. Returns the state after.
 */
export async function balanceTestEntries(): Promise<CapitalState> {
  const state = await snapshotCapital();
  if (state.testNet === 0) return state;
  const fix = -state.testNet;
  const text = `${fix < 0 ? "-" : ""}${Math.floor(Math.abs(fix) / 100)}.${String(Math.abs(fix) % 100).padStart(2, "0")}`;
  await addTestEntry("adjustment", text, `e2e rebalance ${RUN_ID}`);
  const after = await snapshotCapital();
  if (after.testNet !== 0) throw new Error(`test entries still net ${after.testNet} cents after rebalancing`);
  return after;
}

/** The entries `notePrefix` marks (this run's). */
export async function capitalEntriesNoted(notePrefix: string) {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase
      .from("bankroll_entries")
      .select("id, mode, kind, amount, note, is_test")
      .like("note", `${notePrefix}%`);
    if (error) throw new Error(`entries read failed: ${error.message}`);
    return data.map((r) => ({
      id: String(r.id),
      mode: r.mode as string,
      kind: r.kind as string,
      amount: cents(r.amount),
      isTest: r.is_test === true,
    }));
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

// ------------------------------------------------------------------- Wags

/** A venture's slug by its exact name (read-only). */
export async function ventureSlugNamed(name: string): Promise<string | null> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase.from("ventures").select("slug").eq("name", name).maybeSingle();
    if (error) throw new Error(`venture lookup failed: ${error.message}`);
    return (data?.slug as string | undefined) ?? null;
  });
}

/** Goals with exactly this title: what a confirmed Wags proposal created. */
export async function goalIdsTitled(title: string): Promise<string[]> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase.from("goals").select("id").eq("title", title);
    if (error) throw new Error(`goal lookup failed: ${error.message}`);
    return data.map((g) => g.id as string);
  });
}

/** Delete exactly these goals (by id, as the spec recorded them). */
export async function deleteGoalIds(ids: string[]) {
  if (!ids.length) return;
  await asOwner((supabase) => deleteGoalsById(supabase, ids));
}

/**
 * Delete exactly the Wags threads a spec created, by id. Their messages go
 * with them (db/029's cascade, the only delete wags_messages allows).
 */
export async function deleteWagsThreads(ids: string[]) {
  const unique = [...new Set(ids.filter(Boolean))];
  if (!unique.length) return;
  await asOwner(async (supabase) => {
    const { error } = await supabase.from("wags_threads").delete().in("id", unique);
    if (error) throw new Error(`thread cleanup failed: ${error.message}`);
  });
}

/** A thread's stored messages, oldest first: what survives a reload. */
export async function wagsMessages(threadId: string): Promise<{ role: string; content: string; model: string | null }[]> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase
      .from("wags_messages")
      .select("role, content, model")
      .eq("thread_id", threadId)
      .order("id");
    if (error) throw new Error(`message read failed: ${error.message}`);
    return data as { role: string; content: string; model: string | null }[];
  });
}

// ------------------------------------------------------------ Morning brief

/**
 * A TEST brief (db/030 add_test_brief, the verified marker): is_test, so no
 * real view, no ctx_brief and no delivery ever sees it. Briefs are
 * append-only, so it stays; only a marked request shows it.
 */
export async function addTestBrief(day: string, body: { headline: string; sections: { key: string; title: string; lines: string[] }[]; one_move: string }): Promise<number> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase.rpc("add_test_brief", { p_marker: e2eMarker(), p_brief_date: day, p_body: body });
    if (error) throw new Error(`test brief failed: ${error.message}`);
    return data as number;
  });
}

/** A TEST feed story (db/032 add_test_story): is_test, one item from the
 * `_e2e` source, linking `url` (a .invalid host). Invisible to the real feed;
 * a marked request shows it. `summary` null makes a headline-only card. */
export async function addTestStory(opts: { pillar: "home" | "sports"; title: string; summary: string | null; url: string }): Promise<number> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase.rpc("add_test_story", {
      p_marker: e2eMarker(),
      p_pillar: opts.pillar,
      p_title: opts.title,
      p_summary: opts.summary,
      p_url: opts.url,
    });
    if (error) throw new Error(`test story failed: ${error.message}`);
    return data as number;
  });
}

/** A TEST sports story with a sport (db/042 add_test_sports_story): as
 * addTestStory, pillar sports, for the chips. */
export async function addTestSportsStory(opts: { sport: "cfb" | "nfl" | "nba" | "f1" | "golf"; title: string; summary: string | null; url: string }): Promise<number> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase.rpc("add_test_sports_story", {
      p_marker: e2eMarker(),
      p_sport: opts.sport,
      p_title: opts.title,
      p_summary: opts.summary,
      p_url: opts.url,
    });
    if (error) throw new Error(`test sports story failed: ${error.message}`);
    return data as number;
  });
}

/** A TEST Home story with a category (db/042 add_test_news_story): as
 * addTestStory, pillar home, for the /news chips. */
export async function addTestNewsStory(opts: {
  category: "deals" | "hospitality" | "sports_business" | "film_music" | "luxury_marine" | "betting" | "other";
  title: string;
  summary: string | null;
  url: string;
}): Promise<number> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase.rpc("add_test_news_story", {
      p_marker: e2eMarker(),
      p_category: opts.category,
      p_title: opts.title,
      p_summary: opts.summary,
      p_url: opts.url,
    });
    if (error) throw new Error(`test news story failed: ${error.message}`);
    return data as number;
  });
}

/** A TEST tech story (db/045 add_test_tech_story): is_test, from the official
 * test source `_e2e-tech` (so its card carries the Official tag), pillar
 * tech, with a category for the /tech chips. Invisible to the real feed; a
 * marked request shows it. */
export async function addTestTechStory(opts: {
  category: "models_features" | "pricing_credits" | "tooling" | "agents" | "stack" | "community" | "other";
  title: string;
  summary: string | null;
  url: string;
}): Promise<number> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase.rpc("add_test_tech_story", {
      p_marker: e2eMarker(),
      p_category: opts.category,
      p_title: opts.title,
      p_summary: opts.summary,
      p_url: opts.url,
    });
    if (error) throw new Error(`test tech story failed: ${error.message}`);
    return data as number;
  });
}

/** A TEST GitHub repo story (db/049 add_test_repo): a repo, its story in
 * category repos (pillar tech) and its surfacing, all is_test, from the
 * `_e2e-repos` source with a .invalid link. Shown only to a request with the
 * verified e2e marker (Latest); never in For you, the cap, the resurface
 * rule, Wags or the brief. Rows are permanent; names carry the run prefix. */
export async function addTestRepo(opts: {
  fullName: string;
  summary: string | null;
  stars: number;
  stars7d: number | null;
  stars7dSource: "measured" | "trending_weekly" | "trending_daily" | "since_created" | "age_average" | null;
}): Promise<number> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase.rpc("add_test_repo", {
      p_marker: e2eMarker(),
      p_full_name: opts.fullName,
      p_summary: opts.summary,
      p_stars: opts.stars,
      p_stars_7d: opts.stars7d,
      p_stars_7d_source: opts.stars7dSource,
    });
    if (error) throw new Error(`test repo failed: ${error.message}`);
    return data as number;
  });
}

/** A TEST title (db/051 add_test_title): is_test, a tmdb_id from 900000000
 * up (never a real one), released 10 days from today, on no shelf. Shown only
 * with the verified e2e marker. Rows are permanent; names carry the run prefix. */
export async function addTestTitle(opts: { kind: "movie" | "tv"; name: string }): Promise<{ id: number; tmdbId: number }> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase.rpc("add_test_title", { p_marker: e2eMarker(), p_kind: opts.kind, p_name: opts.name });
    if (error) throw new Error(`test title failed: ${error.message}`);
    const t = await supabase.from("titles").select("tmdb_id").eq("id", data as number).single();
    if (t.error) throw new Error(`test title read failed: ${t.error.message}`);
    return { id: data as number, tmdbId: (t.data as { tmdb_id: number }).tmdb_id };
  });
}

/** A TEST trailer story (db/051 add_test_trailer, pillar screening) from the
 * `_e2e-screening` source with a .invalid link, matched to a test title or
 * unmatched. */
export async function addTestTrailer(opts: { title: string; url: string; titleId: number | null }): Promise<number> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase.rpc("add_test_trailer", {
      p_marker: e2eMarker(), p_title: opts.title, p_url: opts.url, p_title_id: opts.titleId,
    });
    if (error) throw new Error(`test trailer failed: ${error.message}`);
    return data as number;
  });
}

/** A TEST recommendation (db/053 add_test_rec): a test title scored at the
 * top of For you, "because" a second test title named `because`. Shown only
 * with the marker. Returns the recommended title's id. */
export async function addTestRec(opts: { kind: "movie" | "tv"; name: string; because: string }): Promise<number> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase.rpc("add_test_rec", {
      p_marker: e2eMarker(), p_kind: opts.kind, p_name: opts.name, p_because: opts.because,
    });
    if (error) throw new Error(`test rec failed: ${error.message}`);
    return data as number;
  });
}

/** A TEST seen-it pass title (db/053 add_test_seen_title): first in the pass
 * and recommended until answered. Returns its id. */
export async function addTestSeenTitle(opts: { kind: "movie" | "tv"; name: string }): Promise<number> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase.rpc("add_test_seen_title", { p_marker: e2eMarker(), p_kind: opts.kind, p_name: opts.name });
    if (error) throw new Error(`test seen title failed: ${error.message}`);
    return data as number;
  });
}

/** A title's title_reactions rows (db/053), oldest first. */
export async function titleReactions(titleId: number): Promise<{ reaction: string; seen: boolean; origin: string }[]> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase.from("title_reactions").select("reaction, seen, origin").eq("title_id", titleId).order("id");
    if (error) throw new Error(`title reaction read failed: ${error.message}`);
    return data as { reaction: string; seen: boolean; origin: string }[];
  });
}

/** Retire a spec's test recommendation: Not interested, so it leaves For you
 * for every later run (test rows are permanent and all score 49). */
export async function retireTestRec(titleId: number): Promise<void> {
  return asOwner(async (supabase) => {
    const { data: me } = await supabase.auth.getUser();
    const { error } = await supabase.from("title_reactions").insert({
      owner_id: me.user!.id, title_id: titleId, reaction: "not_interested", origin: "card",
    });
    if (error) throw new Error(`retire test rec failed: ${error.message}`);
  });
}

/** A TEST Arsenal reference (db/054 add_test_arsenal_ref): is_test, a .invalid
 * source, already settled. Shown only with the marker. Returns its id. */
export async function addTestArsenalRef(opts: {
  category: string; title: string; room: string; verification?: "verified" | "corrected" | "unverified";
  note?: string | null; quote?: string | null;
}): Promise<number> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase.rpc("add_test_arsenal_ref", {
      p_marker: e2eMarker(), p_category: opts.category, p_title: opts.title, p_room: opts.room,
      p_verification: opts.verification ?? "verified", p_note: opts.note ?? null, p_quote: opts.quote ?? null,
    });
    if (error) throw new Error(`test arsenal ref failed: ${error.message}`);
    return data as number;
  });
}

/** A TEST Power Word (db/054 add_test_arsenal_word). Returns its id. */
export async function addTestArsenalWord(lemma: string): Promise<number> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase.rpc("add_test_arsenal_word", { p_marker: e2eMarker(), p_lemma: lemma });
    if (error) throw new Error(`test arsenal word failed: ${error.message}`);
    return data as number;
  });
}

/** A TEST drop for today of these test references (slot order) and word
 * (db/054 add_test_arsenal_drop; the drop guard holds it to every rule). */
export async function addTestArsenalDrop(refIds: number[], wordId: number): Promise<number> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase.rpc("add_test_arsenal_drop", { p_marker: e2eMarker(), p_ref_ids: refIds, p_word_id: wordId });
    if (error) throw new Error(`test arsenal drop failed: ${error.message}`);
    return data as number;
  });
}

/** A reference's arsenal_reactions rows, oldest first, with is_test. */
export async function arsenalReactions(refId: number): Promise<{ reaction: string; is_test: boolean }[]> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase.from("arsenal_reactions").select("reaction, is_test").eq("ref_id", refId).order("id");
    if (error) throw new Error(`arsenal reaction read failed: ${error.message}`);
    return (data ?? []) as { reaction: string; is_test: boolean }[];
  });
}

/** A title's history (watch_status), oldest first: what the spec checks. */
export async function watchHistory(titleId: number): Promise<{ status: string; finished_at: string | null }[]> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase.from("watch_status").select("status, finished_at").eq("title_id", titleId).order("id");
    if (error) throw new Error(`watch history read failed: ${error.message}`);
    return (data ?? []) as { status: string; finished_at: string | null }[];
  });
}

/** A TEST move (db/042 add_test_move_proposal): "Test Player <s>" on "Test
 * Team A <s>" and an open trade to "Test Team B <s>", all is_test. Shown on
 * /settings/feed with a `test` tag; never a real entity. Rows are permanent,
 * so a spec decides what it creates. */
export async function addTestMoveProposal(suffix: string): Promise<number> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase.rpc("add_test_move_proposal", { p_marker: e2eMarker(), p_suffix: suffix });
    if (error) throw new Error(`test move failed: ${error.message}`);
    return data as number;
  });
}

/** Decide a proposal as the owner (db/042), for a spec's cleanup. */
export async function decideTestMove(id: number, decision: "confirmed" | "dismissed"): Promise<void> {
  return asOwner(async (supabase) => {
    const { error } = await supabase.rpc("decide_entity_link_proposal", { p_id: id, p_decision: decision });
    if (error && !/already/.test(error.message)) throw new Error(`decide failed: ${error.message}`);
  });
}

/** A story's feed_reactions rows (db/039), oldest first: what the buttons,
 * swipes, Undo and the open beacon wrote. Append-only, so they stay; they
 * hang off a test story, which no real profile ever reads. */
export async function storyReactions(storyId: number): Promise<{ pillar: string; reaction: string }[]> {
  return asOwner(async (supabase) => {
    const { data, error } = await supabase
      .from("feed_reactions")
      .select("pillar, reaction")
      .eq("story_id", storyId)
      .order("id");
    if (error) throw new Error(`reaction read failed: ${error.message}`);
    return data as { pillar: string; reaction: string }[];
  });
}

/** The owner's local date (America/Chicago), as the app computes it. */
export function chicagoDay(offsetDays = 0): string {
  const d = new Date(Date.now() + offsetDays * 86_400_000);
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Chicago", year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
}
