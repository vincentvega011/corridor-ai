import { defineConfig, devices } from "@playwright/test";

// Own ports (API 8100, web 3100) and own database, so the browser tests never touch a running `make demo`
// (API 8000, web 3000). Override with E2E_API_PORT and E2E_WEB_PORT.
const API_PORT = process.env.E2E_API_PORT ?? "8100";
const WEB_PORT = process.env.E2E_WEB_PORT ?? "3100";
const DB = process.env.E2E_DATABASE_URL ?? `postgresql+psycopg://corridor:corridor@localhost:${process.env.CORRIDOR_PG_PORT ?? "5432"}/corridor_e2e`;
const REPLAY_ENV: Record<string, string> = process.env.E2E_RECORD_ROLES ? { LLM_RECORD_ROLES: process.env.E2E_RECORD_ROLES } : {};

// Chromium only, against a production build. The planner, reader and parser run in cassette replay mode
// (LLM_REPLAY=1): no model server is started and nothing leaves the machine.
export default defineConfig({
  testDir: "./e2e",
  timeout: 240_000,
  expect: { timeout: 15_000 },
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "off",
    ...devices["Pixel 7"],
    browserName: "chromium",
    viewport: { width: 380, height: 780 },
  },
  projects: [{ name: "chromium" }],
  globalSetup: "./e2e/global-setup.ts",
  webServer: [
    {
      command: `uv run uvicorn corridor.api.main:app --host 127.0.0.1 --port ${API_PORT}`,
      cwd: "../backend",
      url: `http://127.0.0.1:${API_PORT}/healthz`,
      reuseExistingServer: false,
      timeout: 90_000,
      env: { DATABASE_URL: DB, APP_ENV: "demo", DEMO_CLOCK: "on", LLM_REPLAY: "1", ...REPLAY_ENV },
    },
    {
      command: `pnpm exec next start -p ${WEB_PORT}`,
      url: `http://127.0.0.1:${WEB_PORT}/manifest.webmanifest`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { BACKEND_URL: `http://127.0.0.1:${API_PORT}` },
    },
  ],
});
