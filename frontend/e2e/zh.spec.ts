import path from "node:path";
import { expect, test } from "@playwright/test";
import { ROOT, choosePersona, confirmOpenCard, customerChat, goTo, officerToken, resetSandbox, say, setLocale, shotZh } from "./helpers";

test.beforeEach(() => resetSandbox());

// Simplified Chinese is display only: the parser, planner and composer get the same language and payload as in English,
// so the recorded cassettes still match and the offline demo lines work with the UI in Chinese.
test("zh: the recorded housing deposit line still gives the card and the hold card, with the UI in Chinese", async ({ page }) => {
  await choosePersona(page, "usr_priya");
  await setLocale(page, "zh");
  await expect(page.getByTestId("sandbox-banner")).toContainText("沙盒环境：无真实资金");
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await expect(page.getByTestId("deva-digits")).toHaveCount(0); // the Devanagari digit option is hidden
  await shotZh(page, "00-chat-home");

  await page.getByTestId("attach-input").setInputFiles(path.join(ROOT, "data", "fixtures", "rt", "rt_housing_deposit.pdf"));
  await expect(page.getByTestId("attached-note")).toContainText("不可信");
  await say(page, "Please pay this deposit for the room");
  const card = page.getByTestId("proposal-card").last();
  await expect(card).toBeVisible(); // not the "assistant unavailable" fallback: the cassettes still match
  await expect(card).toContainText("请核对每一项信息");
  await expect(card.getByTestId("card-warnings")).toBeVisible();
  await expect(card.getByTestId("card-level")).toContainText("需要您的验证码");
  await shotZh(page, "02-confirm-card");

  await confirmOpenCard(page, "usr_priya", true);
  const hold = page.getByTestId("hold-card");
  await expect(hold).toBeVisible();
  await expect(hold).toContainText("此收款人已被暂缓");
  await expect(hold.getByTestId("hold-until")).toContainText("年");
  await expect(hold.getByTestId("hold-until")).toContainText(/GMT\+11|AEDT/);
  await expect(page.getByTestId("msg-assistant").last()).toContainText("已保存收款人"); // the short line is Chinese too
  await shotZh(page, "03-hold-card");
});

test("zh: routes, tuition, passport and the console trace render Chinese", async ({ page, browser }) => {
  await choosePersona(page, "usr_priya");
  await setLocale(page, "zh");

  await goTo(page, "汇款路线");
  await page.getByTestId("routes-amount").fill("2000");
  await page.getByTestId("routes-compare").click();
  await expect(page.getByTestId("routes-result")).toBeVisible();
  await expect(page.getByTestId("route-recommended")).toContainText("推荐");
  await expect(page.getByTestId("route-arrival").first()).toContainText("年");
  await shotZh(page, "04-routes");

  await goTo(page, "学费");
  await expect(page.getByTestId("plan")).toBeVisible();
  await expect(page.getByTestId("plan-step")).toHaveCount(3);
  await expect(page.getByTestId("plan-rate-source")).toContainText("汇率");
  await shotZh(page, "05-tuition");

  await goTo(page, "信用护照");
  await page.getByTestId("passport-issue").click();
  await confirmOpenCard(page, "usr_priya");
  await expect(page.getByTestId("passport-held")).toBeVisible();
  await page.getByTestId("claim-rent_on_time").check();
  await expect(page.getByTestId("share-preview")).toContainText("只会分享这些");
  await shotZh(page, "06-passport");

  // the bank console in Chinese, in a desktop browser context (the choice is the same preference the app stores)
  const { proposalHash } = await customerChat("usr_priya", "Please pay this deposit for the room", { file: path.join(ROOT, "data", "fixtures", "rt", "rt_housing_deposit.pdf") });
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, baseURL: process.env.E2E_BASE_URL ?? `http://127.0.0.1:${process.env.E2E_WEB_PORT ?? "3100"}` });
  await ctx.addInitScript(() => window.localStorage.setItem("corridor_prefs", JSON.stringify({ locale: "zh", deva: false })));
  const desk = await ctx.newPage();
  await desk.goto("/console/login");
  await desk.getByTestId("login-officer").selectOption("officer_asha");
  await desk.getByTestId("login-credential").fill(officerToken("officer_asha"));
  await desk.getByTestId("login-submit").click();
  await expect(desk.getByTestId("officer-id")).toHaveText("officer_asha");
  await expect(desk.getByTestId("lang-zh")).toHaveAttribute("aria-pressed", "true");
  await desk.getByRole("link", { name: "提案追踪" }).click();
  await desk.getByTestId("trace-select").selectOption(proposalHash);
  await expect(desk.getByTestId("trace-graph")).toContainText("确定性核心");
  await expect(desk.getByTestId("trace-graph")).toContainText("文件输入：不可信");
  await shotZh(desk, "07-console-trace", "console");
  await ctx.close();
});
