"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { formatDate, formatOfficerTime, formatTime } from "@/lib/format";
import en from "@/messages/en.json";
import ne from "@/messages/ne.json";
import zh from "@/messages/zh.json";
import consoleEn from "@/messages/console.en.json";
import consoleZh from "@/messages/console.zh.json";

export type Locale = "en" | "ne" | "zh";
const CATALOGS: Record<Locale, Record<string, string>> = { en, ne, zh };
// the bank console is English, with a Simplified Chinese version (Nepali shows the English console)
const CONSOLE: Record<string, Record<string, string>> = { en: consoleEn, zh: consoleZh };
const DEVA = "०१२३४५६७८९";
const PREF_KEY = "corridor_prefs"; // language and digit style only

type Params = Record<string, string | number>;
interface I18n {
  locale: Locale;
  setLocale: (l: Locale) => void;
  devaDigits: boolean;
  setDevaDigits: (v: boolean) => void;
  /** What the model-facing API expects: the UI language, except Chinese, which is display only (chat stays English and Nepali). */
  apiLang: "en" | "ne";
  /** A bank console string (English, or Simplified Chinese when the UI is Chinese). */
  tc: (key: string, params?: Params) => string;
  /** A time in the officer's zone (Nepal time), the zone named, in the UI language. */
  fmtOfficerTime: (iso: string) => string;
  /** A message from the locale files. Unknown keys show the key (never hide a string). */
  t: (key: string, params?: Params) => string;
  /** Show digits in the chosen style. Display only: never used on a hash, an id or a value sent to the server. */
  num: (s: string) => string;
  /** A date in the user's language (Nepali month names, chosen digits). */
  fmtDate: (iso: string) => string;
  /** An instant in the given zone, zone named, no seconds. */
  fmtTime: (iso: string, zone: string) => string;
}

const Ctx = createContext<I18n | null>(null);

export function toDevaDigits(s: string): string {
  return s.replace(/[0-9]/g, (d) => DEVA.charAt(Number(d)));
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>("en");
  const [devaDigits, setDevaState] = useState(false);

  useEffect(() => {
    try {
      // eslint-disable-next-line no-restricted-properties -- language and digit style only, never a token
      const saved = JSON.parse(window.localStorage.getItem(PREF_KEY) ?? "{}") as { locale?: Locale; deva?: boolean };
      // eslint-disable-next-line react-hooks/set-state-in-effect -- reading saved preferences once after mount
      if (saved.locale === "ne" || saved.locale === "en" || saved.locale === "zh") setLocaleState(saved.locale);
      if (typeof saved.deva === "boolean") setDevaState(saved.deva);
    } catch {
      /* no saved preference */
    }
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale === "ne" ? "ne" : locale === "zh" ? "zh-CN" : "en";
  }, [locale]);

  const persist = (l: Locale, d: boolean) => {
    try {
      // eslint-disable-next-line no-restricted-properties -- language and digit style only, never a token
      window.localStorage.setItem(PREF_KEY, JSON.stringify({ locale: l, deva: d }));
    } catch {
      /* private mode: the choice lasts for this visit */
    }
  };
  const setLocale = useCallback((l: Locale) => { setLocaleState(l); persist(l, devaDigits); }, [devaDigits]);
  const setDevaDigits = useCallback((v: boolean) => { setDevaState(v); persist(locale, v); }, [locale]);

  const value = useMemo<I18n>(() => {
    // the digit option applies only in Nepali: in English every number is Western even if the option is saved as on
    const useDeva = devaDigits && locale === "ne";
    const num = (s: string) => (useDeva ? toDevaDigits(s) : s);
    const t = (key: string, params?: Params) => {
      const raw = CATALOGS[locale][key] ?? CATALOGS.en[key] ?? key;
      const filled = raw.replace(/\{(\w+)\}/g, (_, k: string) => String(params?.[k] ?? `{${k}}`));
      return num(filled);
    };
    const tc = (key: string, params?: Params) => {
      const raw = (locale === "zh" ? CONSOLE.zh?.[key] : undefined) ?? CONSOLE.en?.[key] ?? key;
      return raw.replace(/\{(\w+)\}/g, (_, k: string) => String(params?.[k] ?? `{${k}}`));
    };
    return {
      locale, setLocale, devaDigits, setDevaDigits, t, num, tc,
      apiLang: locale === "zh" ? "en" : locale,
      fmtDate: (iso) => formatDate(iso, locale, useDeva),
      fmtTime: (iso, zone) => formatTime(iso, zone, locale, useDeva),
      fmtOfficerTime: (iso) => formatOfficerTime(iso, locale === "zh" ? "zh" : "en"),
    };
  }, [locale, devaDigits, setLocale, setDevaDigits]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useI18n(): I18n {
  const v = useContext(Ctx);
  if (!v) throw new Error("useI18n outside I18nProvider");
  return v;
}
