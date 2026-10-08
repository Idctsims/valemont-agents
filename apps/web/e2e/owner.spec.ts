import { expect, test, type Page } from "@playwright/test";

// Signed-in checks, skipped unless the owner's credentials are in the shell
// session (never in a file):
//   $env:E2E_OWNER_EMAIL = '...'; $env:E2E_OWNER_PASSWORD = '...'
// Each project signs in once, so a full run makes two real sign-ins.
const email = process.env.E2E_OWNER_EMAIL;
const password = process.env.E2E_OWNER_PASSWORD;

test.skip(!email || !password, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
test.describe.configure({ mode: "serial" });

async function signIn(page: Page, next = "/command/betting") {
  await page.goto(`/login?next=${encodeURIComponent(next)}`);
  await page.getByLabel("Email").fill(email!);
  await page.getByLabel("Password").fill(password!);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(new RegExp(`${next}$`));
}

async function setTheme(page: Page, theme: "night" | "day") {
  await page.context().addCookies([
    { name: "vm-theme", value: theme, url: page.url() },
  ]);
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
}

test("owner signs in, sees the active pillar, and logs out", async ({ page }, testInfo) => {
  await signIn(page);
  await expect(page.getByRole("heading", { level: 1, name: "Betting" })).toBeVisible();

  const phone = testInfo.project.name === "phone";
  if (phone) {
    const tab = page.getByRole("navigation", { name: "Sections" }).getByRole("link", {
      name: "Sports",
    });
    await expect(tab).toHaveAttribute("aria-current", "page");
  } else {
    const item = page.getByRole("navigation", { name: "Pillars" }).getByRole("link", {
      name: /Betting/,
    });
    await expect(item).toHaveAttribute("aria-current", "page");
  }

  for (const theme of ["night", "day"] as const) {
    await setTheme(page, theme);
    await page.screenshot({
      path: `e2e-screenshots/pillar-betting-${testInfo.project.name}-${theme}.png`,
    });
  }

  // Log out: phone through the account menu, desktop from the rail.
  if (phone) {
    await page.getByRole("button", { name: "Account" }).click();
  }
  await page.getByRole("button", { name: "Log out" }).filter({ visible: true }).click();
  await expect(page).toHaveURL(/\/login$/);

  // The session is really gone, not just the page.
  await page.goto("/command");
  await expect(page).toHaveURL(/\/login\?next=%2Fcommand$/);
});
