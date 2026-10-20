/** Turn what the server sends (ids, codes, English fixed phrases) into the words a customer reads. Display only. */

type T = (key: string, params?: Record<string, string | number>) => string;

/** A key-safe form of a fixed server phrase, matching the `val.*` keys in the locale files. */
export function slug(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 60);
}

/** "AUD spending account ****1234" in the user's language. Anything else is returned unchanged. */
export function accountText(label: string, t: T): string {
  const m = /^([A-Z]{3}) (spending|savings) account (\S+)$/.exec(label);
  return m ? t("acct.label", { ccy: m[1] ?? "", kind: t(`acct.kind.${m[2]}`), no: m[3] ?? "" }) : label;
}

/** "Ramesh (uncle, Doha)" from a name, a relation and a region code. Missing parts are left out. */
export function whoText(name: string | null | undefined, relation: string | null | undefined, region: string | null | undefined, t: T): string {
  const n = name || t("common.unknownWho");
  const rel = relation ? (t(`rel.${relation}`) === `rel.${relation}` ? relation : t(`rel.${relation}`)) : null;
  const city = region ? (t(`city.${region}`) === `city.${region}` ? region : t(`city.${region}`)) : null;
  const parts = [rel, city].filter(Boolean);
  return parts.length ? `${n} (${parts.join(", ")})` : n;
}

/** A fixed or semi-fixed phrase from a confirmation card, translated when we know it. */
export function phraseText(v: string, t: T, fmtDate: (iso: string) => string): string {
  let m = /^Payee from document (\*+\d+)$/.exec(v);
  if (m) return t("card.val.docPayee", { no: m[1] ?? "" });
  if (/^[A-Z]{3} (spending|savings) account \S+$/.test(v)) return accountText(v, t);
  m = /^into (.+)$/.exec(v);
  if (m) return t("card.val.into", { label: accountText(m[1] ?? "", t) });
  m = /^(.+) \(a request, no money moves from you\)$/.exec(v);
  if (m) return t("card.val.requestTo", { who: whoFromServer(m[1] ?? "", t) });
  m = /^(.+) via (.+), about (\d+) hours after they agree$/.exec(v);
  if (m) return t("card.val.arrives", { amount: m[1] ?? "", route: m[2] ?? "", h: m[3] ?? "" });
  m = /^(.*?), (.+) \(due (\d{4}-\d{2}-\d{2})\)$/.exec(v);
  if (m) return `${m[1]}, ${phraseText(m[2] ?? "", t, fmtDate)} (${t("card.val.due", { date: fmtDate(m[3] ?? "") })})`;
  m = /^(.*?), due (\d{4}-\d{2}-\d{2})$/.exec(v);
  if (m) return `${m[1]}, ${t("card.val.due", { date: fmtDate(m[2] ?? "") })}`;
  if (/^\d{4}-\d{2}-\d{2}$/.test(v)) return fmtDate(v);
  const key = `val.${slug(v)}`;
  const hit = t(key);
  return hit === key ? v : hit;
}

/** The server writes a contributor as "Ramesh (uncle, QA)": show it with the relation and city in words. */
function whoFromServer(s: string, t: T): string {
  const m = /^(.+?) \(([a-z_]+), ([A-Z]{2})\)$/.exec(s);
  return m ? whoText(m[1], m[2], m[3], t) : s;
}

/** "Harbourview University, Semester 2, 2026": the issuer is a proper name, the description is translated. */
export function invoiceText(label: string, t: T): string {
  const m = /^(.*?), (.+)$/.exec(label);
  if (!m) return label;
  const key = `val.${slug(m[2] ?? "")}`;
  const hit = t(key);
  return `${m[1]}, ${hit === key ? m[2] : hit}`;
}

/** Devanagari digits to Western digits, then everything that is not a digit removed. A typed code is always Western. */
export function westernDigits(s: string): string {
  return s.replace(/[\u0966-\u096F]/g, (d) => String(d.charCodeAt(0) - 0x0966)).replace(/\D/g, "");
}
