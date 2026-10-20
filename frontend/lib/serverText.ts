import pairs from "@/messages/server.zh.json";

/** Escape a string for use inside a regular expression. */
const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

interface Rule { re: RegExp; names: string[]; zh: string }

// one rule per server template: the English text with each {name} as a capture group
const RULES: Rule[] = (pairs as { id: string; en: string; zh: string }[]).map((p) => {
  const names: string[] = [];
  const src = p.en.split(/(\{\w+\})/).map((part) => {
    const m = /^\{(\w+)\}$/.exec(part);
    if (!m) return esc(part);
    names.push(m[1] ?? "");
    return "([\\s\\S]*?)";
  }).join("");
  return { re: new RegExp(`^${src}$`), names, zh: p.zh };
});

/**
 * The Simplified Chinese version of a fixed server reply, when the text is one of the server's English templates
 * (display only: the server still understands and answers in English or Nepali). Text that is not a template
 * (for example a model-composed sentence) is returned unchanged.
 */
export function serverZh(text: string): string {
  for (const r of RULES) {
    const m = r.re.exec(text);
    if (!m) continue;
    const vals: Record<string, string> = {};
    r.names.forEach((n, i) => { vals[n] = m[i + 1] ?? ""; });
    return r.zh.replace(/\{(\w+)\}/g, (_, k: string) => vals[k] ?? `{${k}}`);
  }
  return text;
}
