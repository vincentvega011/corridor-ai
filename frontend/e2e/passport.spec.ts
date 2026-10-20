import { expect, test } from "@playwright/test";
import { choosePersona, confirmOpenCard, resetSandbox, setLocale, shot, goTo } from "./helpers";

test.beforeEach(() => resetSandbox());

test("passport: issue, share with a landlord's challenge, and a forwarded copy is rejected", async ({ page, browser }) => {
  // the landlord (a different browser context, no login) issues a challenge
  const landlord = await (await browser.newContext({ viewport: { width: 380, height: 780 } })).newPage();
  await landlord.goto("/verify");
  await landlord.getByTestId("verify-audience").fill("Mr Okafor");
  await landlord.getByTestId("verify-challenge").click();
  const nonce = (await landlord.getByTestId("verify-nonce").textContent())?.trim() ?? "";
  expect(nonce).toHaveLength(32);
  await shot(landlord, "30-verifier-challenge", "en");

  // Priya issues her passport and shares one claim, answering that challenge
  await choosePersona(page, "usr_priya");
  await goTo(page, "Credit Passport");
  await page.getByTestId("passport-issue").click();
  await confirmOpenCard(page, "usr_priya");
  await expect(page.getByTestId("passport-held")).toBeVisible();
  await page.getByTestId("claim-rent_on_time").check();
  await page.getByTestId("passport-audience").fill("Mr Okafor");
  await page.getByTestId("passport-challenge").fill(nonce);
  await page.getByTestId("passport-present").click();
  await expect(page.locator('[data-testid="proposal-card"][data-state="open"]').last()).toContainText("Mr Okafor");
  await shot(page, "31-passport-share-card", "en");
  await confirmOpenCard(page, "usr_priya");
  const presentation = (await page.getByTestId("presentation-text").inputValue()).trim();
  expect(JSON.parse(presentation).audience).toBe("Mr Okafor");
  await shot(page, "32-passport-presentation", "en");

  // the landlord checks it: valid, for him, and only the rent claim
  await landlord.getByTestId("verify-input").fill(presentation);
  await landlord.getByTestId("verify-check").click();
  const ok = landlord.getByTestId("verify-result");
  await expect(ok).toHaveAttribute("data-valid", "yes");
  await expect(ok).toContainText("rent");
  await shot(landlord, "33-verifier-valid", "en");

  // forwarded to another landlord: it names someone else, so it fails; used twice, it fails again
  const other = await (await browser.newContext({ viewport: { width: 380, height: 780 } })).newPage();
  await other.goto("/verify");
  await other.getByTestId("verify-audience").fill("Ms Brown");
  await other.getByTestId("verify-input").fill(presentation);
  await other.getByTestId("verify-check").click();
  const bad = other.getByTestId("verify-result");
  await expect(bad).toHaveAttribute("data-valid", "no");
  await expect(bad).toContainText("made for someone else");
  await shot(other, "34-verifier-forwarded-rejected", "en");
  await landlord.getByTestId("verify-check").click();
  await expect(landlord.getByTestId("verify-result")).toContainText("already used");
  await setLocale(other, "ne");
  await shot(other, "34-verifier-forwarded-rejected", "ne");
});
