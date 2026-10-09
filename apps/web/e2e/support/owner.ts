import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, type Page } from "@playwright/test";
import { createClient } from "@supabase/supabase-js";

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

/**
 * Delete every goal an owner spec made, as the owner (RLS applies). Goals are
 * an app table, so this is real cleanup, children first: a carried goal
 * cannot be deleted while its carried row exists (db/021).
 */
export async function deleteE2eGoals() {
  const { url, key } = publicEnv();
  const supabase = createClient(url, key, { auth: { persistSession: false } });
  const { error: authError } = await supabase.auth.signInWithPassword({ email: email!, password: password! });
  if (authError) throw new Error(`cleanup sign-in failed: ${authError.message}`);
  for (const children of [true, false]) {
    let q = supabase.from("goals").delete().like("title", `${E2E_PREFIX}%`);
    q = children ? q.not("carried_from", "is", null) : q.is("carried_from", null);
    const { error } = await q;
    if (error) throw new Error(`cleanup failed: ${error.message}`);
  }
  await supabase.auth.signOut();
}
