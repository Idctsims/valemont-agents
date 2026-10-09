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
  await page.context().addCookies([{ name: "vm-theme", value: theme, url: page.url() }]);
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

/** Run `fn` with a supabase-js client signed in as the owner (RLS applies). */
async function asOwner<T>(fn: (supabase: SupabaseClient) => Promise<T>): Promise<T> {
  const { url, key } = publicEnv();
  const supabase = createClient(url, key, { auth: { persistSession: false } });
  const { error } = await supabase.auth.signInWithPassword({ email: email!, password: password! });
  if (error) throw new Error(`owner sign-in failed: ${error.message}`);
  try {
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
 * Delete goals an owner spec made, as the owner (RLS applies): those titled
 * with `titlePrefix`, or (seed: true) those noted e2e-seed. Goals are an app
 * table, so this is real cleanup. Leaves first: a goal cannot be deleted
 * while its carried row exists (db/021), and a seed may be carried twice.
 */
export async function deleteE2eGoals(scope: { titlePrefix?: string; seed?: boolean }) {
  await asOwner(async (supabase) => {
    for (let pass = 0; pass < 10; pass++) {
      const reads = [
        scope.titlePrefix
          ? supabase.from("goals").select("id, carried_from").like("title", `${scope.titlePrefix}%`)
          : null,
        scope.seed ? supabase.from("goals").select("id, carried_from").eq("notes", SEED_NOTE) : null,
      ].filter((q) => q !== null);
      const results = await Promise.all(reads);
      const failed = results.find((r) => r.error);
      if (failed?.error) throw new Error(`cleanup read failed: ${failed.error.message}`);
      const data = results.flatMap((r) => r.data ?? []);
      if (!data.length) return;
      const parents = new Set(data.map((g) => g.carried_from).filter(Boolean));
      const leaves = data.filter((g) => !parents.has(g.id)).map((g) => g.id);
      const del = await supabase.from("goals").delete().in("id", leaves);
      if (del.error) throw new Error(`cleanup failed: ${del.error.message}`);
    }
    throw new Error("cleanup: e2e goals still present after 10 passes");
  });
}

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
 * Every row is marked SEED_NOTE and removed by deleteE2eGoals().
 */
export async function seedScreens(thisWeek: string, lastWeek: string, twoWeeksAgo: string, month: string) {
  const seeds: Seed[] = [
    { title: "Send Clipd the revised term sheet", horizon: "weekly", period: thisWeek, area: "business" },
    { title: "Draft the Sail Beach Club budget", horizon: "weekly", period: thisWeek, area: "business" },
    { title: "Four lifts this week", horizon: "weekly", period: thisWeek, area: "health", status: "done" },
    { title: "Book the Excursion site visit", horizon: "weekly", period: thisWeek, status: "done" },
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
      for (let i = 0; i < (s.carries ?? 0); i++) {
        const carried = await supabase.rpc("carry_goal", { p_goal: id });
        if (carried.error) throw new Error(`seed carry failed: ${carried.error.message}`);
        const child = await supabase.from("goals").select("id").eq("carried_from", id).single();
        if (child.error) throw new Error(`seed carry read failed: ${child.error.message}`);
        id = child.data.id;
      }
    }
  });
}
