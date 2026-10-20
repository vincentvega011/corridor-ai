import { execFileSync } from "node:child_process";
import { createHmac } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { expect, type Page } from "@playwright/test";

export const ROOT = path.resolve(__dirname, "..", "..");
const DB = process.env.E2E_DATABASE_URL ?? `postgresql+psycopg://corridor:corridor@localhost:${process.env.CORRIDOR_PG_PORT ?? "5432"}/corridor_e2e`;
const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? "8100"}`;
export const SCREENS = path.join(ROOT, "docs", "evidence", "screens");

/** Put the sandbox back to its seed (ledger, payees, locks, grants) so every test starts the same. */
export function resetSandbox() {
  execFileSync("uv", ["run", "python", "-m", "corridor.sandbox.demo_reset"], {
    cwd: path.join(ROOT, "backend"),
    env: { ...process.env, DATABASE_URL: DB, APP_ENV: "demo" },
    stdio: "pipe",
  });
}

/** One pending request from Priya to her uncle (the plan's first step, without running the planner). */
export function seedContributionRequest() {
  execFileSync("uv", ["run", "python", "../scripts/e2e_seed_request.py"], {
    cwd: path.join(ROOT, "backend"),
    env: { ...process.env, DATABASE_URL: DB, APP_ENV: "demo" },
    stdio: "pipe",
  });
}

export function tamperLedgerRow(seq: number) {
  execFileSync("uv", ["run", "python", "../scripts/e2e_tamper.py", String(seq)], {
    cwd: path.join(ROOT, "backend"),
    env: { ...process.env, DATABASE_URL: DB, APP_ENV: "demo" },
    stdio: "pipe",
  });
}

/** A customer action done through the API (the browser tests then look at the bank's side). Returns the events. */
export async function customerChat(persona: Persona, message: string, opts?: { file?: string; confirm?: boolean }) {
  const token = tokens()[persona];
  const docs: { doc_id: string }[] = [];
  if (opts?.file) {
    const up = await fetch(`${API}/attachments`, { method: "POST", headers: { authorization: `Bearer ${token}`, "content-type": "application/pdf" }, body: fs.readFileSync(opts.file) });
    docs.push({ doc_id: ((await up.json()) as { doc_id: string }).doc_id });
  }
  const read = async (res: Response) => {
    const events: { type: string; data: Record<string, unknown> }[] = [];
    for (const block of (await res.text()).split("\n\n")) {
      const line = block.split("\n").find((l) => l.startsWith("data:"));
      if (line) events.push(JSON.parse(line.slice(5)));
    }
    return events;
  };
  const res = await fetch(`${API}/chat`, { method: "POST", headers: { authorization: `Bearer ${token}`, "content-type": "application/json" }, body: JSON.stringify({ message, attachments: docs }) });
  const thread = res.headers.get("x-thread-id");
  let events = await read(res);
  const card = events.find((e) => e.type === "confirm_request");
  if (opts?.confirm && card && thread) {
    const totp = card.data.required_level === "mfa" ? await freshCode(persona) : undefined;
    const done = await fetch(`${API}/chat/${thread}/confirm`, { method: "POST", headers: { authorization: `Bearer ${token}`, "content-type": "application/json" }, body: JSON.stringify({ proposal_hash: card.data.proposal_hash, totp }) });
    events = [...events, ...(await read(done))];
  }
  return { events, proposalHash: String(card?.data.proposal_hash ?? "") };
}

const tokens = (): Record<string, string> =>
  JSON.parse(fs.readFileSync(path.join(ROOT, "secrets", "demo_tokens.json"), "utf-8")) as Record<string, string>;
export type Officer = "officer_asha" | "officer_binod";
export const officerToken = (id: Officer = "officer_asha") =>
  (JSON.parse(fs.readFileSync(path.join(ROOT, "secrets", "officer_tokens.json"), "utf-8")) as Record<string, string>)[id] ?? "";

export type Persona = "usr_priya" | "usr_ramesh" | "usr_anil";

export async function api(persona: Persona | "officer", method: "GET" | "POST", url: string, body?: unknown) {
  const token = persona === "officer" ? officerToken() : tokens()[persona];
  const res = await fetch(`${API}${url}`, {
    method,
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: res.status, json: (await res.json().catch(() => null)) as Record<string, unknown> | null };
}

// RFC 6238 (SHA-1, 30 seconds, 6 digits) from the sandbox's synthetic seed. The demo API has no code helper
// (that route exists in dev only), and the demo clock is the server's, so the time comes from GET /clock.
function base32(input: string): Buffer {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of input.replace(/=+$/, "").toUpperCase()) bits += alphabet.indexOf(c).toString(2).padStart(5, "0");
  const out: number[] = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) out.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(out);
}

function totpAt(seed: string, epochSeconds: number): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(epochSeconds / 30)));
  const hmac = createHmac("sha1", base32(seed)).update(counter).digest();
  const offset = (hmac[hmac.length - 1] ?? 0) & 0xf;
  const bin = ((hmac[offset] ?? 0) & 0x7f) * 2 ** 24 + ((hmac[offset + 1] ?? 0) << 16) + ((hmac[offset + 2] ?? 0) << 8) + (hmac[offset + 3] ?? 0);
  return String(bin % 1_000_000).padStart(6, "0");
}

const lastCode: Record<string, string> = {};
/** The current authenticator code of a persona, waiting for a new 30 second window if it was used already. */
export async function freshCode(persona: Persona): Promise<string> {
  const seed = fs.readFileSync(path.join(ROOT, "secrets", "mfa", `${persona}.seed`), "utf-8").trim();
  for (let i = 0; i < 70; i++) {
    const clock = (await (await fetch(`${API}/clock`)).json()) as { now: string };
    const code = totpAt(seed, Date.parse(clock.now) / 1000);
    if (code !== lastCode[persona]) {
      lastCode[persona] = code;
      return code;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error("no fresh authenticator code");
}

export async function choosePersona(page: Page, persona: Persona) {
  await page.goto("/");
  await page.getByTestId("persona-select").selectOption(persona);
  await expect(page.getByTestId("persona-select")).toHaveValue(persona);
}

export async function setLocale(page: Page, locale: "en" | "ne" | "zh") {
  await page.getByTestId(`lang-${locale}`).click();
}

export async function shot(page: Page, name: string, locale: "en" | "ne") {
  fs.mkdirSync(SCREENS, { recursive: true });
  await fullShot(page, path.join(SCREENS, `${name}.${locale}.png`));
}

export async function say(page: Page, text: string) {
  await page.getByTestId("chat-input").fill(text);
  await page.getByTestId("send-button").click();
}

/** Type the authenticator code into the newest open card (if it asks for one) and confirm. */
export async function confirmOpenCard(page: Page, persona: Persona, mfa?: boolean) {
  const open = page.locator('[data-testid="proposal-card"][data-state="open"]');
  const done = page.locator('[data-testid="proposal-card"][data-state="done"]');
  await expect(open.last()).toBeVisible();
  const before = await done.count();
  const card = open.last();
  const needs = mfa ?? (await card.getByTestId("totp-input").count()) > 0;
  if (needs) await card.getByTestId("totp-input").fill(await freshCode(persona));
  await card.getByTestId("confirm-button").click();
  await expect(done).toHaveCount(before + 1);
}

export function money(db_user: string): Promise<{ status: number; json: Record<string, unknown> | null }> {
  return api(db_user as Persona, "GET", "/accounts");
}

/** Pay a saved payee from the Payees screen with the form (a button flow: no parser and no planner). */
export async function payFromPayees(page: Page, payeeName: string, amount: string) {
  await page.goto("/payees");
  const row = page.getByTestId("payee-row").filter({ hasText: payeeName });
  await expect(row).toBeVisible();
  await row.getByTestId("payee-pay-amount").fill(amount);
  await row.getByTestId("payee-pay").click();
}

/** Follow a main navigation link by name. Below 1024 px the secondary pages sit behind the "More" tab. */
export async function goTo(page: Page, name: string) {
  const link = page.getByRole("link", { name });
  if (!(await link.first().isVisible())) await page.getByTestId("nav-more").click();
  await link.first().click();
}

/** A full page capture with fixed bars (bottom navigation, message dock) at the bottom of the page, where they sit for a user. */
export async function fullShot(page: Page, file: string) {
  const size = page.viewportSize();
  const h = await page.evaluate(() => document.documentElement.scrollHeight);
  if (!size) return void (await page.screenshot({ path: file, fullPage: true }));
  await page.setViewportSize({ width: size.width, height: Math.min(h, 6000) });
  await page.waitForTimeout(150);
  await page.screenshot({ path: file });
  await page.setViewportSize(size);
}

/** A button flow done through the API (ADR 0021): no model parses or plans it. Returns the proposal hash. */
export async function customerAction(persona: Persona, action: Record<string, unknown>, label = "Button") {
  const token = tokens()[persona];
  const res = await fetch(`${API}/chat`, { method: "POST", headers: { authorization: `Bearer ${token}`, "content-type": "application/json" }, body: JSON.stringify({ message: label, action }) });
  const text = await res.text();
  const card = text.split("\n\n").map((b) => b.split("\n").find((l) => l.startsWith("data:"))).filter(Boolean).map((l) => JSON.parse((l as string).slice(5)) as { type: string; data: Record<string, unknown> }).find((e) => e.type === "confirm_request");
  return { proposalHash: String(card?.data.proposal_hash ?? "") };
}

/** A Simplified Chinese evidence screenshot: docs/evidence/screens/zh/<name>.png */
export async function shotZh(page: Page, name: string, subdir = "") {
  const dir = path.join(SCREENS, "zh", subdir);
  fs.mkdirSync(dir, { recursive: true });
  await fullShot(page, path.join(dir, `${name}.png`));
}
