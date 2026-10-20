import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { ROOT, api, customerChat, officerToken, resetSandbox, tamperLedgerRow, fullShot, customerAction } from "./helpers";

test.use({ viewport: { width: 1280, height: 800 } });
test.beforeEach(() => resetSandbox());

const SHOTS = path.join(ROOT, "docs", "evidence", "screens", "console");
const shot = (page: Page, name: string) => fullShot(page, path.join(SHOTS, `${name}.png`));

async function login(page: Page, officer: "officer_asha" | "officer_binod" = "officer_asha") {
  await page.goto("/console/login");
  await page.getByTestId("login-officer").selectOption(officer);
  await page.getByTestId("login-credential").fill(officerToken(officer));
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("officer-id")).toHaveText(officer);
}

async function payeeId(name: string): Promise<string> {
  const r = await api("usr_priya", "GET", "/payees");
  const p = ((r.json?.payees ?? []) as { payee_id: string; display_name: string }[]).find((x) => x.display_name === name);
  if (!p) throw new Error(`no payee ${name}`);
  return p.payee_id;
}

async function typedLandlordRequest() {
  await customerChat("usr_priya", "Add my new landlord Okafor Property, account 062000771234", { confirm: true });
  const id = await payeeId("Okafor Property");
  expect((await api("usr_priya", "POST", `/payees/${id}/verification-request`, {})).status).toBe(200);
  return id;
}

test("the officer verifies a typed landlord after a CoP match plus a callback, and it is ledgered", async ({ page }) => {
  const id = await typedLandlordRequest();
  await login(page, "officer_binod");
  await page.getByRole("link", { name: "Verification queue" }).click();
  const item = page.getByTestId("queue-item").first();
  await expect(item.getByTestId("queue-name")).toHaveText("Okafor Property");
  await expect(item.getByTestId("queue-cop")).toContainText("match");
  await shot(page, "01-verification-queue");
  await item.getByTestId("queue-open").click();
  await expect(page.getByTestId("cop-result")).toContainText("matches");
  await expect(page.getByTestId("verify-submit")).toBeDisabled(); // no reference yet: all three are required
  await page.getByTestId("verify-method").selectOption("callback_independent_number");
  await page.getByTestId("verify-reference").fill("Called the landlord's agency on the number in the state register, spoke to Ms Brown");
  await shot(page, "02-verification-form");
  await page.getByTestId("verify-submit").click();
  await expect(page.getByTestId("verify-notice")).toContainText("Verified");
  const payees = (await api("usr_priya", "GET", "/payees")).json?.payees as { payee_id: string; verified: boolean }[];
  expect(payees.find((p) => p.payee_id === id)?.verified).toBe(true);

  // the ledger holds the method, the reference, the CoP result and the officer's own id
  await page.getByRole("link", { name: "Ledger" }).click();
  await page.getByTestId("ledger-user").fill("usr_priya");
  await page.getByTestId("ledger-filter").click();
  const row = page.getByTestId("ledger-row").filter({ hasText: "execution" }).first();
  await row.getByText("payload").click();
  await expect(row).toContainText("officer_binod");
  await expect(row).toContainText("callback_independent_number");
  await expect(row).toContainText("cop");
});

test("a CoP mismatch cannot be verified, in the console or by the API", async ({ page }) => {
  await customerChat("usr_priya", "Please pay this deposit for the room", { file: path.join(ROOT, "data", "fixtures", "rt", "rt_housing_deposit.pdf"), confirm: true });
  const r = await api("usr_priya", "GET", "/payees");
  const scam = ((r.json?.payees ?? []) as { payee_id: string; display_name: string }[]).find((p) => p.display_name.startsWith("Payee from document"));
  expect(scam).toBeTruthy();
  await api("usr_priya", "POST", `/payees/${scam!.payee_id}/verification-request`, {});
  await login(page);
  await page.getByRole("link", { name: "Verification queue" }).click();
  const item = page.getByTestId("queue-item").first();
  await expect(item).toHaveAttribute("data-cop", "mismatch");
  await item.getByTestId("queue-open").click();
  await expect(page.getByTestId("cop-result")).toContainText("does NOT match");
  await expect(page.getByTestId("verify-blocked")).toBeVisible();
  await expect(page.getByTestId("verify-reference")).toBeDisabled();
  await shot(page, "03-cop-mismatch-blocked");
  const refused = await api("officer", "POST", `/officer/payees/${scam!.payee_id}/verify`, { method: "in_branch_check", reference: "the agent insisted it is fine" });
  expect(refused.status).toBe(409);
  expect(refused.json?.detail).toBe("cop_mismatch");
});

test("tampering with one ledger row makes the chain check fail", async ({ page }) => {
  await customerChat("usr_priya", "Add my new landlord Okafor Property, account 062000771234", { confirm: true });
  await login(page);
  await page.getByRole("link", { name: "Ledger" }).click();
  await page.getByTestId("verify-chain").click();
  await expect(page.getByTestId("chain-result")).toHaveAttribute("data-valid", "yes");
  await shot(page, "04-ledger-chain-pass");
  tamperLedgerRow(2);
  await page.getByTestId("verify-chain").click();
  await expect(page.getByTestId("chain-result")).toHaveAttribute("data-valid", "no");
  await expect(page.getByTestId("chain-result")).toContainText("FAIL");
  await expect(page.getByTestId("chain-result")).toContainText("entry 2");
  await shot(page, "05-ledger-chain-fail");
});

test("a customer token is rejected on every officer route, in the API and through the console gateway", async ({ page, request }) => {
  const customer = (await import("node:fs")).readFileSync(path.join(ROOT, "secrets", "demo_tokens.json"), "utf-8");
  const priya = (JSON.parse(customer) as Record<string, string>).usr_priya;
  for (const url of ["/officer/me", "/officer/ledger", "/officer/risk/queue", "/officer/verification-requests", "/officer/cases", "/officer/eval/summary"]) {
    const res = await fetch(`http://127.0.0.1:${process.env.E2E_API_PORT ?? "8100"}${url}`, { headers: { authorization: `Bearer ${priya}` } });
    expect(res.status, url).toBe(401);
  }
  // the console gateway needs the officer's own cookie: without it, 401; a customer cookie is no officer cookie
  expect((await request.get("/api/console/officer/ledger")).status()).toBe(401);
  await page.goto("/console/login");
  await page.getByTestId("login-credential").fill(priya);
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("login-error")).toBeVisible();
});

test("the trace of the housing scam shows the document as untrusted and the rules that fired", async ({ page }) => {
  const { proposalHash } = await customerChat("usr_priya", "Please pay this deposit for the room", { file: path.join(ROOT, "data", "fixtures", "rt", "rt_housing_deposit.pdf") });
  expect(proposalHash).toHaveLength(64);
  await login(page);
  await page.getByRole("link", { name: "Proposal trace" }).click();
  await page.getByTestId("trace-select").selectOption(proposalHash);
  await expect(page.getByTestId("trace-hash")).toHaveText(proposalHash);
  await expect(page.getByTestId("trace-graph")).toBeVisible();
  await shot(page, "06-trace-housing-scam");
  await page.getByText("Show step list").click();
  const doc = page.getByTestId("trace-step").filter({ hasText: "Document input: UNTRUSTED" });
  await expect(doc).toHaveAttribute("data-tone", "untrusted");
  await expect(page.getByTestId("trace-step").filter({ hasText: "Rule R-PAYEE-ADD" }).first()).toBeVisible();
  await expect(page.getByTestId("trace-step").filter({ hasText: "Rule R-INJECTION" }).first()).toBeVisible();
  await expect(page.getByTestId("trace-step").filter({ hasText: "Planner proposal" })).toContainText("UNTRUSTED");
  await expect(page.getByTestId("trace-step").filter({ hasText: "Parser output" })).toHaveCount(1);
  await expect(page.getByTestId("trace-step").filter({ hasText: "Button action" })).toHaveCount(0); // a chat proposal has a parser and a planner
  const node = page.getByTestId("trace-graph").locator('[data-testid^="trace-node-doc"]').first();
  await expect(node).toHaveAttribute("data-tone", "untrusted"); // the React Flow node itself is marked
});

test("a denied proposal is traced as denied, with the rule that denied it", async ({ page }) => {
  // a passport request for a passport that does not exist is denied by R-PASSPORT: no planner is involved
  const denied = await api("usr_priya", "POST", "/passport/present", { passport_id: "pp_nope", claims: ["rent_on_time"], audience: "x" });
  expect(denied.status).toBe(409);
  await login(page);
  await page.getByRole("link", { name: "Risk queue" }).click();
  const row = page.getByTestId("risk-row").first();
  await expect(row).toContainText("deny");
  await shot(page, "07-risk-queue");
  await row.getByRole("link", { name: "open" }).click();
  await page.getByText("Show step list").click();
  await expect(page.getByTestId("trace-step").filter({ hasText: "Rule R-PASSPORT" })).toContainText("deny");
  await expect(page.getByTestId("trace-step").filter({ hasText: "Denied by policy: nothing executed" })).toBeVisible();
  await shot(page, "08-trace-denied");
});

test("side by side and the evaluation summary read only the published report", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: "Side by side" }).click();
  await expect(page.getByTestId("report-id")).toContainText("20261011T175100");
  await page.getByTestId("case-select").selectOption("RT-061");
  await expect(page.getByTestId("side-baseline")).toBeVisible();
  await expect(page.getByTestId("side-full").getByTestId("side-outcome").first()).toHaveText(/blocked|contained/);
  const fooled = page.getByTestId("side-baseline").getByTestId("side-result").filter({ hasText: "fooled_user" });
  await expect(fooled.getByTestId("side-moved")).toContainText("NPR 415,890.00"); // the report's own number
  await shot(page, "09-side-by-side");
  await page.getByRole("link", { name: "Evaluation" }).click();
  await expect(page.getByTestId("eval-report")).toContainText("20261011T175100");
  await expect(page.getByTestId("eval-preview")).toBeVisible();
  const cautiousBaseline = page.getByTestId("eval-row").filter({ hasText: "cautious_user" }).filter({ hasText: "baseline" });
  await expect(cautiousBaseline.getByTestId("eval-moved")).toHaveText("NPR 1,372,847.45");
  await shot(page, "10-eval-summary");
});

test("a button action is traced as one deterministic node, with no parser and no planner", async ({ page }) => {
  const { proposalHash } = await customerAction("usr_priya", { kind: "send_money", payee_id: "pay_priya_parents", amount_minor: 20000, currency: "AUD", language: "en" }, "Send AUD 200.00 to Baba");
  expect(proposalHash).toHaveLength(64);
  await login(page);
  await page.getByRole("link", { name: "Proposal trace" }).click();
  await page.getByTestId("trace-select").selectOption(proposalHash);
  await expect(page.getByTestId("trace-hash")).toHaveText(proposalHash);
  const node = page.getByTestId("trace-graph").locator('[data-testid="trace-node-button"]');
  await expect(node).toHaveAttribute("data-zone", "core"); // deterministic, not Model
  await expect(node).toContainText("Button action (no model)");
  await expect(page.getByTestId("trace-graph").locator('[data-testid^="trace-node-parse"]')).toHaveCount(0);
  await expect(page.getByTestId("trace-graph").locator('[data-testid="trace-node-prop"]')).toHaveCount(0);
  await page.getByText("Show step list").click();
  await expect(page.getByTestId("trace-step").filter({ hasText: "Button action (no model)" })).toBeVisible();
  await expect(page.getByTestId("trace-step").filter({ hasText: "Parser output" })).toHaveCount(0);
  await expect(page.getByTestId("trace-step").filter({ hasText: "Planner proposal" })).toHaveCount(0);
  await shot(page, "10-trace-button-action");
});

test("after login the console goes straight to the queue, the risk queue shows a table or the empty state, side by side opens on RT-026", async ({ page }) => {
  await login(page);
  await expect(page).toHaveURL(/\/console\/queue/);
  await expect(page.getByTestId("login-submit")).toHaveCount(0);
  await page.goto("/console/login"); // logged in: the login card never stays
  await expect(page).toHaveURL(/\/console\/queue/);
  await page.getByRole("link", { name: "Risk queue" }).click();
  await expect(page.getByTestId("risk-empty").or(page.getByTestId("risk-row").first())).toBeVisible();
  const [empty, rows] = [await page.getByTestId("risk-empty").count(), await page.getByTestId("risk-row").count()];
  expect(empty > 0 && rows > 0).toBe(false); // the empty state or the table, never both
  await page.getByRole("link", { name: "Side by side" }).click();
  await expect(page.getByTestId("case-select")).toHaveValue("RT-026");
  await expect(page.getByTestId("side-baseline")).toBeVisible();
});
