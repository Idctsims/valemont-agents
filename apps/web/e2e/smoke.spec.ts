import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

// Playwright loads specs as CommonJS, so __dirname, not import.meta.
const webRoot = resolve(__dirname, "..");

test.describe("logged out", () => {
  for (const path of ["/goals", "/goals?view=history", "/ventures", "/ventures/sail-beach-club", "/capital", "/wags", "/betting", "/design", "/onboarding", "/settings/health"]) {
    test(`${path} redirects to /login with next`, async ({ page }) => {
      await page.goto(path);
      await expect(page).toHaveURL(
        (url) => url.pathname === "/login" && url.searchParams.get("next") === path,
      );
    });
  }

  test("/ redirects to /login", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL((url) => url.pathname === "/login" && !url.search);
  });

  test("/api/wags answers no one but the owner, and never reaches the model", async ({ request }) => {
    const res = await request.post("/api/wags", {
      data: { threadId: "00000000-0000-4000-8000-000000000000", message: { parts: [{ type: "text", text: "hi" }] } },
      maxRedirects: 0,
    });
    expect([307, 308]).toContain(res.status());
    expect(res.headers()["location"]).toMatch(/\/login/);
  });

  test("an unknown path is gated too, not a public 404", async ({ page }) => {
    await page.goto("/nowhere");
    await expect(page).toHaveURL((url) => url.pathname === "/login");
  });
});

test.describe("/privacy", () => {
  test("is public and indexable", async ({ page }) => {
    const res = await page.goto("/privacy");
    expect(res?.status()).toBe(200);
    await expect(page).toHaveURL(/\/privacy$/);
    await expect(page.getByRole("heading", { level: 1, name: "Privacy policy" })).toBeVisible();
    await expect(page.getByText("boards:read")).toBeVisible();
    await expect(page.getByRole("link", { name: "duckfanboy@gmail.com" })).toHaveAttribute(
      "href",
      "mailto:duckfanboy@gmail.com",
    );
    expect(res?.headers()["x-robots-tag"]).toBeUndefined();
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", /^index/);
  });
});

test.describe("/login", () => {
  test("has no sign-up, reset or third-party affordance", async ({ page }) => {
    await page.goto("/login");
    const main = page.getByRole("main");
    await expect(main.locator("input:not([type=hidden])")).toHaveCount(2);
    await expect(main.locator('input[type="email"]')).toHaveCount(1);
    await expect(main.locator('input[type="password"]')).toHaveCount(1);
    await expect(main.getByRole("link")).toHaveCount(0);
    await expect(main.getByRole("button")).toHaveCount(1);
    await expect(main).not.toContainText(
      /sign ?up|create (an )?account|register|forgot|reset|magic link|continue with/i,
    );
  });

  test("shows no message on first load", async ({ page }) => {
    await page.goto("/login");
    // Scoped to main: Next's route announcer is also role="alert".
    await expect(page.getByRole("main").getByRole("alert")).toHaveText("");
    await expect(page.getByText("Not authorized.")).toHaveCount(0);
  });

  test("an old ?error= URL shows nothing either", async ({ page }) => {
    await page.goto("/login?error=unauthorized");
    await expect(page.getByText("Not authorized.")).toHaveCount(0);
  });

  test("a rejected sign-in gets the generic message and keeps the email", async ({
    page,
  }, testInfo) => {
    // One real attempt per run, not per project: be gentle with the auth
    // rate limit.
    test.skip(testInfo.project.name !== "desktop", "one attempt per run");
    await page.goto("/login");
    await page.getByLabel("Email").fill("nobody@example.invalid");
    await page.getByLabel("Password").fill("not-a-real-password");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("main").getByRole("alert")).toHaveText(
      "Email or password is incorrect.",
    );
    await expect(page.getByLabel("Email")).toHaveValue("nobody@example.invalid");
    await expect(page.getByLabel("Password")).toHaveValue("");
    await expect(page).toHaveURL(/\/login$/);
  });
});

test.describe("headers and crawlers", () => {
  test("pages carry a nonce CSP and anti-framing headers", async ({ request }) => {
    const res = await request.get("/login");
    const h = res.headers();
    expect(h["content-security-policy"]).toMatch(/script-src 'self' 'nonce-[^']+' 'strict-dynamic'/);
    expect(h["content-security-policy"]).toContain("frame-ancestors 'none'");
    expect(h["x-frame-options"]).toBe("DENY");
    expect(h["referrer-policy"]).toBe("same-origin");
    expect(h["x-robots-tag"]).toBe("noindex, nofollow");
    expect(h["x-powered-by"]).toBeUndefined();
  });

  test("/api/watchdog is token-gated, not login-redirected, and says nothing", async ({ request }) => {
    const attempts: Record<string, string>[] = [
      {},
      { Authorization: "Bearer wrong" },
      { Authorization: "wrong" },
    ];
    for (const headers of attempts) {
      const res = await request.get("/api/watchdog", { headers, maxRedirects: 0 });
      expect(res.status()).toBe(401);
      expect(await res.json()).toEqual({ status: "unauthorized" });
      expect(res.headers()["cache-control"]).toBe("no-store");
      expect(res.headers()["x-robots-tag"]).toBe("noindex, nofollow");
    }
  });

  test("robots.txt allows only /privacy", async ({ request }) => {
    const body = await (await request.get("/robots.txt")).text();
    expect(body).toContain("Allow: /privacy");
    expect(body).toContain("Disallow: /");
  });
});

test.describe("design tokens", () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "a file check, run once");
  });

  for (const script of ["check-tokens", "check-contrast"]) {
    test(`pnpm ${script.replace("-", ":")} passes`, () => {
      const run = spawnSync(process.execPath, [`scripts/${script}.mjs`], {
        cwd: webRoot,
        encoding: "utf8",
      });
      expect(run.status, run.stderr || run.stdout).toBe(0);
    });
  }
});
