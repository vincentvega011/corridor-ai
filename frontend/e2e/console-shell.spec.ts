import { expect, test } from "@playwright/test";
import { officerToken, resetSandbox, SCREENS } from "./helpers";
import path from "node:path";

test.beforeEach(() => resetSandbox());

async function login(page: import("@playwright/test").Page, officer: "officer_asha" | "officer_binod") {
  await page.goto("/console/login");
  await page.getByTestId("login-officer").selectOption(officer);
  await page.getByTestId("login-credential").fill(officerToken(officer));
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("officer-id")).toHaveText(officer);
}

test.use({ viewport: { width: 1280, height: 800 } });

test("each officer logs in with their own credential; a customer token or another officer's does not work", async ({ page }) => {
  await page.goto("/console/queue");
  await expect(page).toHaveURL(/\/console\/login/); // not logged in: sent to the login
  await expect(page.getByTestId("sandbox-banner")).toContainText("SANDBOX: no real money");

  await page.getByTestId("login-officer").selectOption("officer_asha");
  await page.getByTestId("login-credential").fill(officerToken("officer_binod")); // the other officer's credential
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("login-error")).toBeVisible();

  await login(page, "officer_asha");
  const cookies = await page.context().cookies();
  const c = cookies.find((x) => x.name === "corridor_officer");
  expect(c?.httpOnly).toBe(true);
  expect(await page.evaluate(() => document.cookie)).toBe(""); // not readable by script
  await page.screenshot({ path: path.join(SCREENS, "console", "00-login-done.png") });
  await page.getByTestId("logout").click();
  await expect(page).toHaveURL(/\/console\/login/);
});
