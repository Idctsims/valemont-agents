import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

// Playwright loads specs as CommonJS, so __dirname, not import.meta.
const tokens = readFileSync(resolve(__dirname, "../src/styles/tokens.css"), "utf8");
const nightBg = tokens.match(/\[data-theme="night"\]\s*\{[^}]*--vm-bg:\s*(#[0-9a-fA-F]{6})/)?.[1];

test.describe("manifest and icons", () => {
  test("manifest is served with colours from tokens.css", async ({ request }) => {
    const res = await request.get("/manifest.webmanifest");
    expect(res.status()).toBe(200);
    const m = await res.json();
    expect(m).toMatchObject({
      name: "Valemont Command",
      short_name: "Valemont",
      display: "standalone",
      start_url: "/",
      scope: "/",
      orientation: "portrait",
    });
    expect(nightBg).toBeTruthy();
    expect(m.background_color).toBe(nightBg!.toLowerCase());
    expect(m.theme_color).toBe(nightBg!.toLowerCase());
    expect(m.icons.map((i: { purpose: string; sizes: string }) => `${i.sizes}:${i.purpose}`)).toEqual([
      "192x192:any",
      "512x512:any",
      "512x512:maskable",
    ]);
  });

  test("every icon is a public PNG", async ({ request }) => {
    for (const path of [
      "/icons/icon-192.png",
      "/icons/icon-512.png",
      "/icons/maskable-512.png",
      "/icons/badge-96.png",
      "/apple-icon.png",
      "/icon.png",
    ]) {
      const res = await request.get(path, { maxRedirects: 0 });
      expect(res.status(), path).toBe(200);
      expect(res.headers()["content-type"], path).toBe("image/png");
    }
  });

  test("pages carry the installed-app tags", async ({ page }) => {
    await page.goto("/login");
    const meta = (name: string) => page.locator(`meta[name="${name}"]`);
    await expect(meta("apple-mobile-web-app-capable")).toHaveAttribute("content", "yes");
    await expect(meta("apple-mobile-web-app-status-bar-style")).toHaveAttribute(
      "content",
      "black-translucent",
    );
    await expect(meta("theme-color")).toHaveAttribute("content", nightBg!.toLowerCase());
    await expect(meta("viewport")).toHaveAttribute("content", /viewport-fit=cover/);
    await expect(page.locator('link[rel="manifest"]')).toHaveAttribute("href", "/manifest.webmanifest");
    await expect(page.locator('link[rel="apple-touch-icon"]')).toHaveCount(1);
  });
});

test.describe("service worker", () => {
  test("is served publicly with root scope allowed", async ({ request }) => {
    const res = await request.get("/serwist/sw.js", { maxRedirects: 0 });
    expect(res.status()).toBe(200);
    expect(res.headers()["content-type"]).toContain("javascript");
    expect(res.headers()["service-worker-allowed"]).toBe("/");
  });

  test("registers at scope / and caches nothing private", async ({ page }) => {
    await page.goto("/privacy");
    const reg = await page.evaluate(async () => {
      const r = await navigator.serviceWorker.ready;
      return { scope: r.scope, script: r.active?.scriptURL ?? "" };
    });
    expect(new URL(reg.scope).pathname).toBe("/");
    expect(new URL(reg.script).pathname).toBe("/serwist/sw.js");

    // Browse under the worker's control, including pages that redirect to
    // /login, then read back every cached URL.
    await page.reload();
    for (const path of ["/login", "/command", "/privacy", "/design"]) await page.goto(path);
    const cached = await page.evaluate(async () => {
      const urls: string[] = [];
      for (const name of await caches.keys()) {
        const cache = await caches.open(name);
        for (const req of await cache.keys()) urls.push(new URL(req.url).pathname);
      }
      return urls;
    });
    expect(cached.length).toBeGreaterThan(0);
    const unexpected = cached.filter(
      (p) => !p.startsWith("/_next/static/") && !p.startsWith("/icons/") && p !== "/offline",
    );
    expect(unexpected, "only static assets, icons and /offline may be cached").toEqual([]);
  });

  test("offline navigation falls back to /offline", async ({ page, context }) => {
    await page.goto("/privacy");
    await page.evaluate(() => navigator.serviceWorker.ready);
    await page.reload(); // now controlled by the worker
    await context.setOffline(true);
    try {
      await page.goto("/command").catch(() => undefined);
      await expect(page.getByRole("heading", { name: "You're offline." })).toBeVisible();
    } finally {
      await context.setOffline(false);
    }
  });
});
