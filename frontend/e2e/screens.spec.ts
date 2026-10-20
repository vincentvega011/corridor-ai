import { expect, test } from "@playwright/test";
import { choosePersona, resetSandbox, setLocale, shot, goTo } from "./helpers";

test.beforeEach(() => resetSandbox());

// Evidence screenshots of the key screens in English and Nepali (docs/evidence/screens). No model is involved.
for (const locale of ["en", "ne"] as const) {
  test(`key screens in ${locale}`, async ({ page }) => {
    await choosePersona(page, "usr_priya");
    if (locale === "ne") await setLocale(page, "ne");
    await expect(page.getByTestId("sandbox-banner")).toBeVisible();
    await shot(page, "00-chat-home", locale);

    await goTo(page, locale === "en" ? "Payees" : "प्राप्तकर्ता");
    await expect(page.getByTestId("payee-row").first()).toBeVisible();
    await shot(page, "05-payees", locale);

    await goTo(page, locale === "en" ? "Routes" : "पठाउने बाटो");
    await page.getByTestId("routes-amount").fill("2000");
    await page.getByTestId("routes-compare").click();
    await expect(page.getByTestId("routes-result")).toBeVisible();
    await shot(page, "06-routes-priya", locale);

    await goTo(page, locale === "en" ? "Family" : "परिवार");
    await shot(page, "07-family-empty", locale);

    await goTo(page, locale === "en" ? "Credit Passport" : "क्रेडिट पासपोर्ट");
    await expect(page.getByTestId("passport-issue")).toBeVisible();
    await shot(page, "08-passport-empty", locale);

    await goTo(page, locale === "en" ? "Verifier page" : "प्रमाणक पृष्ठ");
    await shot(page, "09-verifier", locale);
  });
}

test("the app talks to nothing but its own origin, and has a strict policy", async ({ page }) => {
  const hosts = new Set<string>();
  const violations: string[] = [];
  page.on("request", (r) => hosts.add(new URL(r.url()).host));
  page.on("console", (m) => { if (m.text().includes("Content Security Policy")) violations.push(m.text()); });
  const res = await page.goto("/");
  const csp = res?.headers()["content-security-policy"] ?? "";
  expect(csp).toContain("default-src 'self'");
  expect(csp).toContain("connect-src 'self'");
  expect(csp).not.toContain("unsafe-inline");
  expect(csp).not.toContain("unsafe-eval");
  await choosePersona(page, "usr_priya");
  await goTo(page, "Payees");
  await expect(page.getByTestId("payee-row").first()).toBeVisible();
  expect([...hosts]).toEqual([`127.0.0.1:${process.env.E2E_WEB_PORT ?? "3100"}`]); // no font host, no CDN, no analytics
  expect(violations).toEqual([]);
  // the tokens never reach script-readable storage
  const stored = await page.evaluate(() => JSON.stringify({ ...window.localStorage, ...window.sessionStorage, cookie: document.cookie }));
  expect(stored).not.toMatch(/demo_|officer_|Bearer/);
  expect(await page.evaluate(() => document.cookie)).toBe(""); // the persona cookie is httpOnly
});

test("text from a model, a document or a payee name is shown as text, never as markup", async ({ page }) => {
  await choosePersona(page, "usr_priya");
  await goTo(page, "Payees");
  await page.getByTestId("payee-name-input").fill('<img src=x onerror="window.__pwned=1">Evil');
  await page.getByTestId("payee-account-input").fill("062000771234");
  await page.getByTestId("payee-save").click();
  const card = page.locator('[data-testid="proposal-card"]').last();
  await expect(card).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined();
  expect(await page.locator("img[src='x']").count()).toBe(0);
  await expect(card).toContainText("<img src=x"); // visible as plain characters
});
