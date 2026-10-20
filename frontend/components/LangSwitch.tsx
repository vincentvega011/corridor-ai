"use client";

import { useI18n } from "@/lib/i18n";

export function LangSwitch({ only }: { only?: readonly ("en" | "ne" | "zh")[] }) {
  const { t, locale, setLocale, devaDigits, setDevaDigits } = useI18n();
  return (
    <div className="flex shrink-0 items-center gap-1.5 text-sm" role="group" aria-label={t("lang.label")}>
      <div className="inline-flex rounded-full border border-line bg-paper p-0.5">
        {(only ?? (["en", "ne", "zh"] as const)).map((l) => (
          <button
            key={l}
            type="button"
            data-testid={`lang-${l}`}
            aria-pressed={locale === l}
            onClick={() => setLocale(l)}
            className={`min-h-9 rounded-full px-2 text-[0.8125rem] font-semibold sm:px-2.5 ${locale === l ? "bg-pine text-on-solid" : "text-ink hover:bg-sand"}`}
          >
            {t(`lang.${l}`)}
          </button>
        ))}
      </div>
      {locale === "ne" ? (
        <label className="flex min-h-9 items-center gap-1 whitespace-nowrap pr-1 text-xs font-semibold" title={t("lang.digits")}>
          <input type="checkbox" className="h-4 w-4 accent-pine" data-testid="deva-digits" aria-label={t("lang.digits")} checked={devaDigits} onChange={(e) => setDevaDigits(e.target.checked)} />
          <span aria-hidden="true">१२३</span>
        </label>
      ) : null}
    </div>
  );
}
