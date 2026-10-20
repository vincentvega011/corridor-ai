// CI check (docs/spec/frontend.md acceptance): the build output and the source must not reference a blocked
// or third-party host (Google fonts or analytics, public CDNs), and must not use unsafe browser APIs.
import fs from "node:fs";
import path from "node:path";

const BLOCKED = [
  "fonts.googleapis", "fonts.gstatic", "gstatic.com", "google-analytics", "googletagmanager",
  "recaptcha", "firebase", "cdn.jsdelivr", "unpkg.com", "cdnjs.cloudflare", "telegram.org", "api.telegram", "wa.me", "whatsapp.com",
];
const SOURCE_BANNED = [/dangerouslySetInnerHTML/, /\.innerHTML\s*=/, /localStorage\.setItem\([^)]*token/i];
const roots = [".next/static", ".next/server/app", "app", "components", "lib", "public"];

let bad = 0;
function walk(dir, fn) {
  if (!fs.existsSync(dir)) return;
  for (const name of fs.readdirSync(dir)) {
    const p = path.join(dir, name);
    const st = fs.statSync(p);
    if (st.isDirectory()) walk(p, fn);
    else if (/\.(js|mjs|css|html|json|ts|tsx|webmanifest|txt)$/.test(name) && st.size < 5_000_000) fn(p);
  }
}
for (const root of roots) {
  walk(root, (file) => {
    if (file.endsWith("contracts.ts")) return;
    const text = fs.readFileSync(file, "utf-8");
    for (const host of BLOCKED) {
      if (text.toLowerCase().includes(host)) {
        console.error(`blocked host "${host}" in ${file}`);
        bad++;
      }
    }
    if (/^(app|components|lib)\//.test(file)) {
      for (const re of SOURCE_BANNED) {
        if (re.test(text)) {
          console.error(`banned pattern ${re} in ${file}`);
          bad++;
        }
      }
    }
  });
}
if (bad) process.exit(1);
console.log("no blocked hosts and no banned patterns");
