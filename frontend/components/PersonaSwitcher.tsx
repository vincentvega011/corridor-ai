"use client";

import { useI18n } from "@/lib/i18n";
import { PERSONAS, useSession, type Persona } from "@/lib/session";
import { Icon } from "@/components/ui/Icon";

/** Demo only: switch between the synthetic personas. A real app would sign the user in. */
export function PersonaSwitcher() {
  const { t } = useI18n();
  const { persona, choose } = useSession();
  return (
    <label className="inline-flex min-w-0 items-center gap-1 rounded-md border border border-line-strong bg-sand py-0.5 pl-2 pr-0.5 text-xs" title={t("persona.note")}>
      <Icon name="user" size={15} className="text-saffron-ink" />
      <span className="sr-only">{t("persona.label")}</span>
      <select
        data-testid="persona-select"
        className="min-h-9 w-full min-w-0 max-w-[10.5rem] truncate rounded-md border border-line bg-surface px-2 py-0.5 text-sm font-medium"
        value={persona ?? ""}
        onChange={(e) => void choose(e.target.value as Persona)}
      >
        <option value="" disabled>
          {t("persona.none")}
        </option>
        {PERSONAS.map((p) => (
          <option key={p} value={p}>
            {t(`persona.${p}`)}
          </option>
        ))}
      </select>
    </label>
  );
}
