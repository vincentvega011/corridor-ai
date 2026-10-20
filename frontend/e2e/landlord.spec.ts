import { expect, test } from "@playwright/test";
import { api, choosePersona, confirmOpenCard, payFromPayees, resetSandbox, say, setLocale, shot, goTo } from "./helpers";

test.beforeEach(() => resetSandbox());


async function payeeId(name: string): Promise<string> {
  const r = await api("usr_priya", "GET", "/payees");
  const p = ((r.json?.payees ?? []) as { payee_id: string; display_name: string }[]).find((x) => x.display_name === name);
  if (!p) throw new Error(`no payee ${name}`);
  return p.payee_id;
}

async function aud(): Promise<number> {
  const r = await api("usr_priya", "GET", "/accounts");
  const acct = ((r.json?.accounts ?? []) as { account_id: string; balance: string }[]).find((a) => a.account_id === "acc_priya_aud")!;
  return Math.round(parseFloat(acct.balance.replace(/[^0-9.]/g, "")) * 100); // "AUD 4,200.00" in minor units
}

test("payees: a payee you type is saved with MFA, held at first, and the bank can verify it", async ({ page }) => {
  await choosePersona(page, "usr_priya");
  await goTo(page, "Payees");
  await page.getByTestId("payee-name-input").fill("Okafor Property");
  await page.getByTestId("payee-account-input").fill("062000771234");
  await page.getByTestId("payee-save").click();
  const card = page.locator('[data-testid="proposal-card"][data-state="open"]').last();
  await expect(card).toBeVisible();
  await expect(card.getByTestId("card-level")).toContainText("authenticator");
  await expect(card.getByTestId("field-to")).toHaveText("****1234"); // the full number is never on the card
  await expect(card.getByTestId("field-source")).toContainText("typed by you");
  await shot(page, "40-typed-payee-card", "en");
  await confirmOpenCard(page, "usr_priya");

  const row = page.getByTestId("payee-row").filter({ hasText: "Okafor Property" });
  await expect(row.getByTestId("payee-prov")).toHaveText("Typed by you");
  await expect(row.getByTestId("payee-status")).toContainText("On hold until");
  await shot(page, "41-typed-payee-saved", "en");

  // ask the bank; an officer verifies after a Confirmation of Payee match; the hold ends for a payee you typed
  await row.getByTestId("payee-ask-bank").click();
  await expect(row.getByTestId("payee-status")).toContainText("Waiting for the bank");
  const queue = await api("officer", "GET", "/officer/verification-requests");
  const items = (queue.json?.items ?? []) as { cop: string; payee_name: string }[];
  expect(items.map((i) => [i.payee_name, i.cop])).toEqual([["Okafor Property", "match"]]);
  const ok = await api("officer", "POST", `/officer/payees/${await payeeId("Okafor Property")}/verify`, { method: "callback_independent_number", reference: "CoP name match, called the landlord on the number from the lease register" });
  expect(ok.status).toBe(200);
  await page.reload();
  await goTo(page, "Payees");
  const after = page.getByTestId("payee-row").filter({ hasText: "Okafor Property" });
  await expect(after.getByTestId("payee-status")).toContainText("Verified");
  await expect(after.getByTestId("payee-status")).toContainText("No hold");
  await shot(page, "42-typed-payee-verified", "en");
});

test("same-day landlord deposit: the cap refuses, the bank verifies, MFA payment goes through (t20)", async ({ page }) => {
  const before = await aud();
  await choosePersona(page, "usr_priya");
  await say(page, "Add my new landlord Okafor Property, account 062000771234");
  await confirmOpenCard(page, "usr_priya");
  await expect(page.getByTestId("hold-card").first()).toBeVisible();

  // the same day: AUD 2,000 is far above the first payment cap
  await payFromPayees(page, "Okafor Property", "2000"); // the form: no parser and no planner
  const hold = page.getByTestId("hold-card").last();
  await expect(hold.getByTestId("hold-cap")).toContainText("NPR 5,000.00"); // the cap, explained
  await expect(hold.getByTestId("hold-until")).toContainText("Earliest allowed");
  await expect(hold.getByTestId("hold-until")).toContainText(/GMT\+11|AEDT/); // her zone, named
  await shot(page, "43-same-day-cap-refusal", "en");
  await hold.getByTestId("ask-bank").click();
  await expect(hold.getByTestId("hold-asked")).toBeVisible();
  expect(await aud()).toBe(before); // nothing moved yet

  const verified = await api("officer", "POST", `/officer/payees/${await payeeId("Okafor Property")}/verify`, { method: "callback_independent_number", reference: "CoP name match, called the landlord on the number from the lease register" });
  expect(verified.status).toBe(200);

  await payFromPayees(page, "Okafor Property", "2000");
  const card = page.locator('[data-testid="proposal-card"][data-state="open"]').last();
  await expect(card.getByTestId("card-level")).toContainText("authenticator");
  await expect(card.getByTestId("field-amount")).toContainText("AUD 2,000.00");
  await shot(page, "44-same-day-pay-card", "en");
  await confirmOpenCard(page, "usr_priya");
  await expect(page.getByTestId("receipt").last()).toContainText("AUD 2,000.00");
  expect(await aud()).toBe(before - 200000);
  await setLocale(page, "ne");
  await shot(page, "44-same-day-pay-card", "ne");
});
