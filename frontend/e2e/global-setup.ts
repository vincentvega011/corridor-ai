import { execFileSync } from "node:child_process";
import path from "node:path";

/** Create and seed the separate e2e database (never the developer's data). */
export default function globalSetup() {
  const db = process.env.E2E_DATABASE_URL ?? `postgresql+psycopg://corridor:corridor@localhost:${process.env.CORRIDOR_PG_PORT ?? "5432"}/corridor_e2e`;
  execFileSync("uv", ["run", "python", "../scripts/e2e_prepare.py"], {
    cwd: path.resolve(__dirname, "..", "..", "backend"),
    env: { ...process.env, DATABASE_URL: db },
    stdio: "inherit",
  });
}
