// A production build (no NEXT_PUBLIC_DEMO_PERSONAS) must not contain the persona switcher, the demo token
// reader or the demo sign-in route logic. Builds into a separate directory and searches its output.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const dist = ".next-prod";
fs.rmSync(dist, { recursive: true, force: true });
const env = { ...process.env, NEXT_DIST_DIR: dist };
delete env.NEXT_PUBLIC_DEMO_PERSONAS;
execFileSync("pnpm", ["exec", "next", "build"], { env, stdio: "pipe" });

const MUST_NOT_CONTAIN = ["persona-select", "demo_tokens.json", "corridor_persona", "demo-authenticator", "demo/authenticator", "Demo authenticator"];
let bad = 0;
function walk(dir) {
  for (const name of fs.readdirSync(dir)) {
    const p = path.join(dir, name);
    const st = fs.statSync(p);
    if (st.isDirectory()) walk(p);
    else if (/\.(js|mjs|json|html|rsc|css|txt)$/.test(name) && st.size < 8_000_000) {
      const text = fs.readFileSync(p, "utf-8");
      for (const needle of MUST_NOT_CONTAIN) {
        if (text.includes(needle)) {
          console.error(`production build contains "${needle}" in ${p}`);
          bad++;
        }
      }
    }
  }
}
for (const sub of ["static", "server"]) walk(path.join(dist, sub));
fs.rmSync(dist, { recursive: true, force: true });
if (bad) process.exit(1);
console.log("the production build has no persona switcher, no demo authenticator and no demo sign in");
