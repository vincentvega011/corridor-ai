import { expect, test } from "@playwright/test";
import { api, choosePersona, confirmOpenCard, resetSandbox, seedContributionRequest, setLocale, shot, goTo } from "./helpers";


test.beforeEach(() => resetSandbox());

test("tuition plan: one confirm per proposal, then the uncle accepts", async ({ page }) => {
  await choosePersona(page, "usr_priya");
  await goTo(page, "Tuition");
  await expect(page.getByRole("heading", { name: "Tuition plan", level: 1 })).toBeVisible();
  const plan = page.getByTestId("plan");
  await expect(plan).toBeVisible();
  await expect(plan.getByTestId("plan-step")).toHaveCount(3); // a request to the uncle and two payments
  await shot(page, "10-tuition-plan", "en");

  await page.getByTestId("plan-start").click();
  // card 1: the request to the uncle (moves no money), card 2 and 3: the payments, each with its own hash
  const hashes: string[] = [];
  for (let step = 1; step <= 3; step++) {
    await expect(page.locator('[data-testid="proposal-card"][data-state="open"]').last()).toBeVisible();
    const open = page.locator('[data-testid="proposal-card"][data-state="open"]').last();
    await expect(open).toContainText(`Step ${step} of 3`);
    hashes.push((await open.getByTestId("card-hash").textContent()) ?? "");
    if (step === 1) await shot(page, "11-plan-card-step1", "en");
    await confirmOpenCard(page, "usr_priya");
  }
  expect(new Set(hashes).size).toBe(3);

  // the request is waiting for Ramesh
  await goTo(page, "Tuition");
  await expect(page.getByTestId("plan-request-status").first()).toHaveText("Waiting");

  // Ramesh, the uncle in Doha, accepts
  await choosePersona(page, "usr_ramesh");
  await goTo(page, "Family");
  const incoming = page.getByTestId("request-incoming").first();
  await expect(incoming).toBeVisible();
  await shot(page, "12-family-incoming", "en");
  await incoming.getByTestId("accept").click();
  await expect(incoming.getByTestId("request-status")).toHaveText("Accepted");

  // and Priya sees the answer in her plan status; accepting moved no money by itself
  await choosePersona(page, "usr_priya");
  await goTo(page, "Tuition");
  await expect(page.getByTestId("plan-request-status").first()).toHaveText("Accepted");
  await shot(page, "13-plan-accepted", "en");
  const accounts = (await api("usr_ramesh", "GET", "/accounts")).json?.accounts as { balance_minor: number }[];
  expect(accounts.length).toBeGreaterThan(0);
});

test("route comparison shows the server's numbers, in Nepali too", async ({ page }) => {
  await choosePersona(page, "usr_ramesh");
  await goTo(page, "Routes");
  await page.getByTestId("routes-from").selectOption("QAR");
  await page.getByTestId("routes-to").selectOption("NPR");
  await page.getByTestId("routes-amount").fill("800");
  await page.getByTestId("routes-compare").click();
  const best = page.getByTestId("route-recommended");
  await expect(best).toBeVisible();
  await expect(best.getByTestId("route-cost")).toContainText("QAR");
  await expect(best.getByTestId("route-received")).toContainText("NPR");
  await shot(page, "20-routes", "en");
  await setLocale(page, "ne");
  await expect(page.getByRole("heading", { name: "पैसा पठाउने बाटो तुलना", level: 1 })).toBeVisible();
  await shot(page, "20-routes", "ne");
});

test("the uncle accepts a request and the student sees the answer; accepting moves no money", async ({ page }) => {
  seedContributionRequest();
  await choosePersona(page, "usr_priya");
  await goTo(page, "Tuition");
  await expect(page.getByTestId("plan")).toBeVisible();
  await expect(page.getByTestId("plan-step")).toHaveCount(3); // a request to the uncle and two payments
  await shot(page, "10-tuition-plan", "en");
  await expect(page.getByTestId("plan-request-status").first()).toHaveText("Waiting");

  await choosePersona(page, "usr_ramesh");
  await goTo(page, "Family");
  const incoming = page.getByTestId("request-incoming").first();
  await expect(incoming).toContainText("QAR 3,332.55");
  await shot(page, "12-family-incoming", "en");
  const before = JSON.stringify((await api("usr_ramesh", "GET", "/accounts")).json);
  await incoming.getByTestId("accept").click();
  await expect(incoming.getByTestId("request-status")).toHaveText("Accepted");
  expect(JSON.stringify((await api("usr_ramesh", "GET", "/accounts")).json)).toBe(before);

  await choosePersona(page, "usr_priya");
  await goTo(page, "Tuition");
  await expect(page.getByTestId("plan-request-status").first()).toHaveText("Accepted");
  await shot(page, "13-plan-accepted", "en");
  await setLocale(page, "ne");
  await shot(page, "13-plan-accepted", "ne");
});

test("the uncle can decline", async ({ page }) => {
  seedContributionRequest();
  await choosePersona(page, "usr_ramesh");
  await goTo(page, "Family");
  await page.getByTestId("decline").click();
  await expect(page.getByTestId("request-incoming").first().getByTestId("request-status")).toHaveText("Declined");
});
