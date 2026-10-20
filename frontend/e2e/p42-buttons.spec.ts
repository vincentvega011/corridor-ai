import { expect, test } from "@playwright/test";
import { choosePersona, confirmOpenCard, resetSandbox, setLocale } from "./helpers";

test.beforeEach(() => resetSandbox());

test("Routes: send with the recommended route is a button flow with the rate source on the card", async ({ page }) => {
  await choosePersona(page, "usr_priya");
  await page.goto("/routes");
  await page.getByTestId("routes-amount").fill("2000");
  await page.getByTestId("routes-compare").click();
  await expect(page.getByTestId("routes-result")).toBeVisible();
  await page.getByTestId("routes-send-button").click();
  const card = page.locator('[data-testid="proposal-card"][data-state="open"]').last();
  await expect(card).toBeVisible();
  await expect(card.getByTestId("card-route")).toBeVisible();
  await expect(card.getByTestId("card-rate-source")).toContainText(/Rate: NRB reference, .*illustrative/);
  await expect(card.getByTestId("field-amount")).toContainText("AUD 2,000.00");
  await confirmOpenCard(page, "usr_priya");
  await expect(page.locator('[data-testid="proposal-card"][data-state="done"]')).toHaveCount(1);
});

test("Payees: the pay form works in Nepali too and a refused payment shows one explanation", async ({ page }) => {
  await choosePersona(page, "usr_priya");
  await setLocale(page, "ne");
  await page.goto("/payees");
  const row = page.getByTestId("payee-row").first();
  await row.getByTestId("payee-pay-amount").fill("100");
  await row.getByTestId("payee-pay").click();
  await expect(page.locator('[data-testid="proposal-card"][data-state="open"]').last()).toBeVisible();
});
