import { expect, test, type Page } from "@playwright/test";

// Signed-in checks, skipped unless the owner's credentials are in the shell
// session (never in a file):
//   $env:E2E_OWNER_EMAIL = '...'; $env:E2E_OWNER_PASSWORD = '...'
// Each project signs in once, so a full run makes two real sign-ins.
const email = process.env.E2E_OWNER_EMAIL;
const password = process.env.E2E_OWNER_PASSWORD;

test.skip(!email || !password, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
test.describe.configure({ mode: "serial" });

async function signIn(page: Page, next = "/goals") {
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

test("onboarding renders for the owner on this device", async ({ page }, testInfo) => {
  await signIn(page, "/onboarding");
  await expect(page.getByRole("heading", { level: 1, name: "Set up your phone" })).toBeVisible();
  if (testInfo.project.name === "phone") {
    // Pixel 7 profile: Android, push supported, not installed.
    await expect(page.getByRole("heading", { name: "Install the app" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Notifications" })).toBeVisible();
    await expect(page.getByText("Step 3 of 3")).toBeVisible();
  } else {
    await expect(page.getByRole("heading", { name: "Set this up on your phone" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Enable notifications" })).toHaveCount(0);
  }
  await page.screenshot({
    path: `e2e-screenshots/onboarding-${testInfo.project.name}.png`,
    fullPage: true,
  });
});

test("onboarding on an iPhone in Safari shows Add to Home Screen", async ({ browser }, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "one iPhone run");
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    userAgent:
      "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    isMobile: true,
    hasTouch: true,
  });
  const page = await context.newPage();
  try {
    await signIn(page, "/onboarding");
    await expect(page.getByRole("heading", { name: "Add to Home Screen" })).toBeVisible();
    await expect(page.getByText("Needs iOS 16.4 or later.")).toBeVisible();
    // Locked until installed: no permission button in a Safari tab.
    await expect(page.getByRole("button", { name: "Enable notifications" })).toHaveCount(0);
    await page.screenshot({ path: "e2e-screenshots/onboarding-iphone-safari.png", fullPage: true });
  } finally {
    await context.close();
  }
});

test("owner signs in, sees the active page, and logs out", async ({ page }, testInfo) => {
  await signIn(page);
  await expect(page.getByRole("heading", { level: 1, name: "Goals" })).toBeVisible();

  const phone = testInfo.project.name === "phone";
  if (phone) {
    const tab = page.getByRole("navigation", { name: "Dock" }).getByRole("link", {
      name: "Goals",
    });
    await expect(tab).toHaveAttribute("aria-current", "page");
  } else {
    const item = page.getByRole("navigation", { name: "Pages" }).getByRole("link", {
      name: "Goals",
    });
    await expect(item).toHaveAttribute("aria-current", "page");
  }

  for (const theme of ["night", "day"] as const) {
    await setTheme(page, theme);
    await page.screenshot({
      path: `e2e-screenshots/goals-${testInfo.project.name}-${theme}.png`,
    });
  }

  // Log out: phone through the account menu, desktop from the rail.
  if (phone) {
    await page.getByRole("button", { name: "Account" }).click();
  }
  await page.getByRole("button", { name: "Log out" }).filter({ visible: true }).click();
  await expect(page).toHaveURL(/\/login$/);

  // The session is really gone, not just the page.
  await page.goto("/goals");
  await expect(page).toHaveURL(/\/login\?next=%2Fgoals$/);
});
