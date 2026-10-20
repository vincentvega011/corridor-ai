/** Display helpers. They only format; they never compute money (the server's text is the amount). */

const NE_MONTHS = ["जनवरी", "फेब्रुअरी", "मार्च", "अप्रिल", "मे", "जुन", "जुलाई", "अगस्ट", "सेप्टेम्बर", "अक्टोबर", "नोभेम्बर", "डिसेम्बर"];
const DEVA = "०१२३४५६७८९";
const deva = (s: string) => s.replace(/[0-9]/g, (d) => DEVA.charAt(Number(d)));

/**
 * An ISO time shown in the given IANA zone with the zone named (for example "AEDT" or "GMT+3") and no
 * seconds, in the user's language. Nepali uses Nepali names for the (Gregorian) months, written by this code
 * rather than by the browser, because not every browser ships Nepali locale data; digits are Devanagari when
 * that option is on.
 */
export function formatTime(iso: string, zone: string, locale: "en" | "ne" | "zh", devaDigits = false): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  if (locale === "zh") {
    const q = new Intl.DateTimeFormat("en-AU", { timeZone: zone, day: "numeric", month: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZoneName: "short" }).formatToParts(d);
    const g = (t: string) => q.find((p) => p.type === t)?.value ?? "";
    return `${g("year")}年${g("month")}月${g("day")}日 ${g("hour")}:${g("minute")} ${g("timeZoneName")}`;
  }
  if (locale === "en") {
    return new Intl.DateTimeFormat("en-AU", {
      timeZone: zone, day: "numeric", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZoneName: "short",
    }).format(d);
  }
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: zone, day: "numeric", month: "numeric", year: "numeric",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZoneName: "short",
  }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  const out = `${get("day")} ${NE_MONTHS[Number(get("month")) - 1] ?? ""} ${get("year")}, ${get("hour")}:${get("minute")} ${get("timeZoneName")}`;
  return devaDigits ? deva(out) : out;
}

/** A calendar date (a date has no zone): Nepali month names in Nepali; Devanagari digits when chosen. */
export function formatDate(iso: string, locale: "en" | "ne" | "zh", devaDigits = false): string {
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  if (locale === "zh") return `${d.getUTCFullYear()}年${d.getUTCMonth() + 1}月${d.getUTCDate()}日`;
  if (locale === "en") return new Intl.DateTimeFormat("en-AU", { timeZone: "UTC", dateStyle: "long" }).format(d);
  const out = `${d.getUTCDate()} ${NE_MONTHS[d.getUTCMonth()] ?? ""} ${d.getUTCFullYear()}`;
  return devaDigits ? deva(out) : out;
}

/** The time zone each demo persona lives in (their home, where they read the message). */
export const ZONE_OF: Record<string, string> = {
  usr_priya: "Australia/Sydney",
  usr_ramesh: "Asia/Qatar",
  usr_anil: "Asia/Shanghai",
};

/**
 * Turn what the user typed ("800", "1,200.50") into integer minor units as a string, without floats.
 * Returns null if it is not a plain amount with at most two decimals. This is input parsing of the user's
 * own words: the server still decides everything and shows its own canonical amount on the card.
 */
export function toMinorUnits(input: string): string | null {
  const cleaned = input.trim().replace(/,/g, "");
  const m = /^(\d{1,12})(?:\.(\d{1,2}))?$/.exec(cleaned);
  if (!m) return null;
  const whole = m[1] ?? "0";
  const frac = (m[2] ?? "").padEnd(2, "0");
  const minor = `${whole}${frac}`.replace(/^0+(?=\d)/, "");
  return minor === "0" ? null : minor;
}

/**
 * Integer minor units as text for display ("200000" and "AUD" give "AUD 2,000.00"). String arithmetic only, no floats:
 * every sandbox currency has two decimals. A value that is not a whole number of minor units is returned unchanged.
 */
export function formatMinor(minor: unknown, currency: unknown): string {
  const raw = typeof minor === "number" ? String(minor) : typeof minor === "string" ? minor : "";
  if (!/^-?\d+$/.test(raw)) return [raw || String(minor ?? ""), currency ? String(currency) : ""].filter(Boolean).join(" ");
  const neg = raw.startsWith("-");
  const digits = raw.replace("-", "").padStart(3, "0");
  const whole = digits.slice(0, -2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${currency ? `${String(currency)} ` : ""}${neg ? "-" : ""}${whole}.${digits.slice(-2)}`;
}

/** The bank officers work in Nepal: every console time is shown in Nepal time, the zone named, no seconds. */
export function formatOfficerTime(iso: string, locale: "en" | "zh" = "en"): string {
  const out = formatTime(iso, "Asia/Kathmandu", locale, false);
  return out.replace(/GMT\+5:45/, "NPT");
}
