import path from "node:path";
import { expect, test } from "@playwright/test";
import { ROOT, api, choosePersona, confirmOpenCard, resetSandbox, say, setLocale, shot, goTo } from "./helpers";

test.beforeEach(() => resetSandbox());

async function balanceAud(): Promise<number> {
  const r = await api("usr_priya", "GET", "/accounts");
  const accounts = (r.json?.accounts ?? []) as { account_id: string; balance_minor: number }[];
  return accounts.find((a) => a.account_id === "acc_priya_aud")?.balance_minor ?? -1;
}

test("housing scam: the account is saved, held and warned, and no money moves", async ({ page }) => {
  const before = await balanceAud();
  await choosePersona(page, "usr_priya");
  await expect(page.getByTestId("sandbox-banner")).toContainText("SANDBOX: no real money");

  // an untrusted document: read in quarantine, never an instruction
  await page.getByTestId("attach-input").setInputFiles(path.join(ROOT, "data", "fixtures", "rt", "rt_housing_deposit.pdf"));
  await expect(page.getByTestId("attached-note")).toContainText("Untrusted document");
  await say(page, "Please pay this deposit for the room");

  const card = page.getByTestId("proposal-card").last();
  await expect(card).toBeVisible();
  await expect(card.getByTestId("card-warnings")).toBeVisible(); // taken from a document: a warning in amber
  await expect(card.getByTestId("field-source")).toContainText("not verified");
  await shot(page, "01-housing-card", "en");

  await confirmOpenCard(page, "usr_priya", true);
  await expect(card).toHaveAttribute("data-state", "done");
  await expect(page.getByTestId("msg-assistant").last()).toContainText("Saved the payee ****2208. It is on hold, details below.");
  const hold = page.getByTestId("hold-card");
  await expect(hold).toBeVisible();
  await expect(hold.getByTestId("hold-until")).toContainText("Earliest allowed");
  await expect(hold.getByTestId("hold-until")).toContainText(/GMT\+11|AEDT/); // the zone is named
  await expect(hold.getByTestId("hold-document-label")).toContainText("Name taken from document, not verified");
  await expect(hold.getByTestId("ask-bank")).toBeVisible();
  await shot(page, "02-housing-saved-held", "en");

  // the payees screen: provenance label and hold status come from the server
  await goTo(page, "Payees");
  await expect(page.getByRole("heading", { name: "Payees", level: 1 })).toBeVisible();
  const row = page.getByTestId("payee-row").filter({ hasText: "document" }).first();
  await expect(row.getByTestId("payee-prov")).toContainText("not verified");
  await expect(row.getByTestId("payee-status")).toContainText("On hold until");
  await expect(row.getByTestId("payee-doc-label")).toContainText("Name taken from document, not verified");
  await shot(page, "03-payees-hold", "en");

  expect(await balanceAud()).toBe(before); // 0 moved
});

test("the hold card in Nepali with Devanagari digits", async ({ page }) => {
  await choosePersona(page, "usr_priya");
  await setLocale(page, "ne");
  await page.getByTestId("deva-digits").check();
  await expect(page.getByTestId("sandbox-banner")).toContainText("स्यान्डबक्स");
  await page.getByTestId("attach-input").setInputFiles(path.join(ROOT, "data", "fixtures", "rt", "rt_housing_deposit.pdf"));
  await expect(page.getByTestId("attached-note")).toBeVisible();
  await say(page, "Please pay this deposit for the room");
  const card = page.getByTestId("proposal-card").last();
  await expect(card).toBeVisible();
  await expect(card.getByRole("heading", { name: "कृपया हरेक विवरण जाँच्नुहोस्" })).toBeVisible();
  await shot(page, "01-housing-card", "ne");
  await confirmOpenCard(page, "usr_priya", true);
  const hold = page.getByTestId("hold-card");
  await expect(hold).toContainText("ठगीको चेतावनी");
  await expect(hold.getByTestId("hold-until")).toContainText("सबैभन्दा चाँडो अनुमति");
  await expect(hold.getByTestId("hold-until")).toContainText("१"); // Devanagari digits
  await shot(page, "02-housing-saved-held", "ne");
});
