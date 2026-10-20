import { expect, test, type Page } from "@playwright/test";
import { choosePersona, confirmOpenCard, freshCode, resetSandbox, setLocale, shot, type Persona } from "./helpers";

test.beforeEach(() => resetSandbox());

// raw ids and fixture suffixes must never reach the customer
const RAW = /\b(acc|pay|inv|usr|prov|fl|req)_[a-z0-9_]+\b|\(fictional\)|, fictional\)/;
// proper names, codes and the product name may stay in Latin letters on the Nepali screens
const LATIN_OK = new Set(["Corridor", "AI", "AUD", "NPR", "QAR", "CNY", "MYR", "USD", "NRB", "Priya", "Ramesh", "Anil", "Harbourview", "University", "Northridge", "Institute", "Chen", "Baba", "Ama", "Suresh", "Noor", "Rooms", "DohaMobile", "Aama", "Mr", "Ms", "Okafor", "PeakRemit", "KathmanduLink", "SwiftHome", "BankWire", "AEDT", "AEST", "GMT", "CST", "NPT", "PDF", "English"]);

async function visibleText(page: Page): Promise<string> {
  return (await page.locator("main").innerText()) + "\n" + (await page.locator("header").innerText());
}

const PAGES: [string, string][] = [["/", "chat"], ["/payees", "payees"], ["/routes", "routes"], ["/tuition", "tuition"], ["/family", "family"], ["/passport", "passport"]];

for (const persona of ["usr_priya", "usr_ramesh", "usr_anil"] as Persona[]) {
  test(`no raw ids or fixture suffixes on any customer screen (${persona})`, async ({ page }) => {
    await choosePersona(page, persona);
    for (const [href] of PAGES) {
      await page.goto(href);
      await page.waitForTimeout(600);
      if (href === "/routes") { await page.getByTestId("routes-compare").click(); await expect(page.getByTestId("routes-result")).toBeVisible(); }
      expect(await visibleText(page), `${persona} ${href}`).not.toMatch(RAW);
    }
  });
}

test("routes: arrival is the demo clock plus the speed in the user's zone, with labelled amounts, rate and source", async ({ page }) => {
  await choosePersona(page, "usr_priya");
  await page.goto("/routes");
  await page.getByTestId("routes-amount").fill("2000");
  await page.getByTestId("routes-compare").click();
  await expect(page.getByTestId("routes-result")).toBeVisible();
  const text = await page.getByTestId("routes-result").innerText();
  // 10 Oct 2026 09:00 Nepal time is 14:15 in Sydney; the 2 hour route (SwiftHome) arrives at about 16:15 the same day
  const swift = page.locator('[data-testid^="route-"]', { hasText: "SwiftHome" }).first();
  await expect(swift).toContainText(/16:\d\d/); // the demo clock runs at the real rate, so the minute drifts
  await expect(swift).toContainText("10 Oct 2026");
  await expect(swift).toContainText("AEDT");
  expect(text).not.toMatch(/\d{2}:\d{2}:\d{2}/); // no seconds
  expect(text).toMatch(/Exchange rate: 1 AUD = 107\.65/);
  expect(text).toMatch(/NRB reference rate, .*illustrative/);
  await expect(page.getByTestId("routes-saving")).toContainText(/^You save .* compared with the most expensive option/);
  await shot(page, "06-routes-priya", "en");
});

test("tuition: the invoice, family and accounts are named, and the totals are labelled", async ({ page }) => {
  await choosePersona(page, "usr_priya");
  await page.goto("/tuition");
  await expect(page.getByTestId("plan")).toBeVisible();
  const t = await page.getByTestId("plan").innerText();
  expect(t).toContain("Harbourview University, Semester 2, 2026");
  expect(t).toContain("Ramesh (uncle, Doha)");
  expect(t).toMatch(/AUD (spending|savings) account \*+\d{4}/);
  expect(t).toContain("Total fees for this plan");
  await expect(page.getByTestId("plan-rate-source")).toContainText(/Rate: NRB reference, .*illustrative/);
  expect(t).toContain("Still owed");
  await shot(page, "10-tuition-plan", "en");
});

test("passport: claims are a readable list with the user's own figures, the card says nothing is shared, and the done card is a one line receipt", async ({ page }) => {
  await choosePersona(page, "usr_priya");
  await page.goto("/passport");
  await page.getByTestId("passport-issue").click();
  await expect(page.getByTestId("card-final").or(page.getByTestId("confirm-button"))).toBeVisible();
  await expect(page.locator('[data-testid="proposal-card"]')).toContainText("nothing is issued");
  await confirmOpenCard(page, "usr_priya");
  await expect(page.getByTestId("passport-held")).toBeVisible();
  const held = await page.getByTestId("passport-held").innerText();
  expect(held).toMatch(/Rent payments made on time: 5 of 6/);
  expect(held).not.toMatch(/[\[\]{}"]/); // never a JSON array
  await page.getByTestId("claim-rent_on_time").check();
  await page.getByTestId("passport-audience").fill("Mr Okafor");
  await page.getByTestId("passport-present").click();
  const card = page.locator('[data-testid="proposal-card"][data-state="open"]').last();
  await expect(card).toContainText("nothing is shared");
  await expect(card.getByTestId("field-shares_only")).toContainText(/Rent payments made on time: 5 of 6/);
  await expect(card.getByTestId("field-shares_only")).not.toContainText("[");
  await shot(page, "31-passport-share-card", "en");
  await confirmOpenCard(page, "usr_priya");
  await expect(page.locator('[data-testid="proposal-card"][data-state="done"]').last()).toHaveCount(1);
  await expect(page.getByTestId("passport-receipt")).toContainText("Shared with Mr Okafor");
  // the confirmed card is collapsed to one line
  expect((await page.locator('[data-testid="proposal-card"][data-state="done"]').last().innerText()).split("\n").length).toBe(1);
});

test("chat: no onboarding sentence once a persona is chosen, and none on the other pages", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("chat-no-persona")).toBeVisible();
  await choosePersona(page, "usr_priya");
  await expect(page.getByTestId("chat-no-persona")).toHaveCount(0);
  for (const [href] of PAGES) {
    await page.goto(href);
    expect(await visibleText(page)).not.toContain("Start by typing a request");
  }
});

test("payees: no fictional suffix, a small synthetic badge instead", async ({ page }) => {
  await choosePersona(page, "usr_priya");
  await page.goto("/payees");
  await expect(page.getByTestId("payee-name").first()).toBeVisible();
  const names = await page.getByTestId("payee-name").allInnerTexts();
  expect(names.join("|")).toContain("Mr. Chen");
  expect(names.join("|")).not.toMatch(/fictional|\(/);
  await expect(page.getByTestId("synthetic-badge").first()).toBeVisible();
});

test("every screen in Nepali has no leftover English except proper names", async ({ page }) => {
  for (const persona of ["usr_priya", "usr_ramesh", "usr_anil"] as Persona[]) {
    await choosePersona(page, persona);
    await setLocale(page, "ne");
    const leftovers: string[] = [];
    for (const [href] of PAGES) {
      await page.goto(href);
      await page.waitForTimeout(600);
      if (href === "/routes") { await page.getByTestId("routes-compare").click(); await expect(page.getByTestId("routes-result")).toBeVisible(); }
      if (href === "/passport") { await page.getByTestId("passport-issue").click().catch(() => undefined); }
      const text = await visibleText(page);
      for (const w of text.match(/[A-Za-z]{3,}/g) ?? []) if (!LATIN_OK.has(w)) leftovers.push(`${persona} ${href}: ${w}`);
    }
    expect([...new Set(leftovers)], "English words left on a Nepali screen").toEqual([]);
  }
});

test("the sandbox authenticator panel shows the current code, only in a demo build", async ({ page }) => {
  await choosePersona(page, "usr_priya");
  await expect(page.getByTestId("demo-authenticator")).toBeVisible();
  await page.getByTestId("demo-authenticator-toggle").click();
  await expect(page.getByTestId("demo-authenticator-code")).toHaveText(/^\d{6}$/);
  await expect(page.getByTestId("demo-authenticator-left")).toContainText(/\d/);
  await expect(page.getByTestId("demo-authenticator")).toContainText("Demo authenticator (sandbox)");
  await shot(page, "50-demo-authenticator", "en");
});

test("the tuition plan names the rate source in Nepali too", async ({ page }) => {
  await choosePersona(page, "usr_priya");
  await setLocale(page, "ne");
  await page.goto("/tuition");
  await expect(page.getByTestId("plan-rate-source")).toContainText("नेपाल राष्ट्र बैंकको सन्दर्भ दर");
});

test("authenticator codes are always Western digits, and the code box accepts Devanagari digits", async ({ page }) => {
  await choosePersona(page, "usr_priya");
  await setLocale(page, "ne");
  await page.getByTestId("deva-digits").check();
  await page.getByTestId("demo-authenticator-toggle").click();
  await expect(page.getByTestId("demo-authenticator-code")).toHaveText(/^[0-9]{6}$/); // never Devanagari
  // an MFA card (saving a payee): type the code in Devanagari digits, the box holds Western digits
  await page.goto("/payees");
  await page.getByTestId("payee-name-input").fill("Okafor Property");
  await page.getByTestId("payee-account-input").fill("062000771234");
  await page.getByTestId("payee-save").click();
  const card = page.locator('[data-testid="proposal-card"][data-state="open"]').last();
  const code = await freshCode("usr_priya");
  const deva = code.replace(/[0-9]/g, (d) => "०१२३४५६७८९".charAt(Number(d)));
  await card.getByTestId("totp-input").fill(deva);
  await expect(card.getByTestId("totp-input")).toHaveValue(code);
  await card.getByTestId("confirm-button").click();
  await expect(page.locator('[data-testid="proposal-card"][data-state="done"]')).toHaveCount(1);
});

test("a Western digit code works too, and the plan names the rate only when it converts money", async ({ page }) => {
  await choosePersona(page, "usr_priya");
  await page.goto("/payees");
  await page.getByTestId("payee-name-input").fill("Okafor Property");
  await page.getByTestId("payee-account-input").fill("062000771234");
  await page.getByTestId("payee-save").click();
  await confirmOpenCard(page, "usr_priya");
  // Priya's plan asks her uncle in QAR: a conversion, so the rate line is there
  await page.goto("/tuition");
  await expect(page.getByTestId("plan-rate-source")).toBeVisible();
  // Anil's plan pays from his own CNY account: no conversion, no rate line
  await choosePersona(page, "usr_anil");
  await page.goto("/tuition");
  await expect(page.getByTestId("plan")).toBeVisible();
  await expect(page.getByTestId("plan-rate-source")).toHaveCount(0);
});

const DEVA_DIGIT = /[०-९]/;

test("the Devanagari digit option applies only in Nepali: English shows Western digits everywhere", async ({ page }) => {
  await choosePersona(page, "usr_priya");
  await setLocale(page, "ne");
  await page.getByTestId("deva-digits").check();
  // Nepali with the option on: the banner date and the page numbers use Devanagari digits
  await page.goto("/routes");
  await page.getByTestId("routes-compare").click();
  await expect(page.getByTestId("routes-result")).toContainText(DEVA_DIGIT);
  // authenticator codes stay Western even then
  await page.getByTestId("demo-authenticator-toggle").click();
  await expect(page.getByTestId("demo-authenticator-code")).toHaveText(/^[0-9]{6}$/);
  // back to English: the option is still saved as on, but nothing on any screen is Devanagari
  await setLocale(page, "en");
  for (const [href] of PAGES) {
    await page.goto(href);
    await page.waitForTimeout(500);
    if (href === "/routes") { await page.getByTestId("routes-compare").click(); await expect(page.getByTestId("routes-result")).toBeVisible(); }
    expect(await page.locator("body").innerText(), href).not.toMatch(DEVA_DIGIT);
  }
});

test("tuition: a persona without an invoice is told so; the no-plan message needs an invoice", async ({ page }) => {
  for (const [persona, hasInvoice] of [["usr_ramesh", false], ["usr_priya", true], ["usr_anil", true]] as [Persona, boolean][]) {
    await choosePersona(page, persona);
    await page.goto("/tuition");
    await page.waitForTimeout(800);
    const text = await page.locator("main").innerText();
    if (hasInvoice) {
      await expect(page.getByTestId("plan")).toBeVisible();
      expect(text).not.toContain("You have no tuition invoices");
    } else {
      expect(text).toContain("You have no tuition invoices");
      await expect(page.getByTestId("plan")).toHaveCount(0);
    }
    expect(text, persona).not.toContain("No plan can fund this invoice");
  }
  await setLocale(page, "ne");
  await choosePersona(page, "usr_ramesh");
  await page.goto("/tuition");
  await expect(page.locator("main")).toContainText("तपाईंको कुनै ट्युसन बिजक छैन");
});
