"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ContributionView, ContributionsResponse } from "@/lib/contracts";
import { whoText } from "@/lib/display";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { Icon } from "@/components/ui/Icon";
import { EmptyState, PageTitle } from "@/components/ui/States";

export default function FamilyPage() {
  const { t } = useI18n();
  const { persona, epoch } = useSession();
  const [data, setData] = useState<ContributionsResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!persona) { setData(null); return; }
    try { setData(await api<ContributionsResponse>("contributions")); } catch { setData(null); }
  }, [persona]);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- load data when the persona changes
  useEffect(() => { void load(); }, [load, epoch]);

  async function answer(id: string, accept: boolean) {
    setBusy(id);
    try {
      await api<ContributionView>(`contributions/${encodeURIComponent(id)}/respond`, { method: "POST", body: { accept } });
      await load();
    } finally {
      setBusy(null);
    }
  }

  const row = (r: ContributionView, mine: boolean) => (
    <li key={r.request_id} data-testid={mine ? "request-outgoing" : "request-incoming"} className="enter card flex flex-col gap-3">
      <div className="flex items-start gap-3">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-md bg-saffron-soft text-saffron-ink" aria-hidden="true"><Icon name={mine ? "send" : "family"} size={19} /></span>
        <div className="min-w-0 flex-1">
          <p className="font-semibold">{mine ? whoText(r.contributor.split(" ")[0], r.contributor_relation, r.contributor_region, t) : t("family.from", { name: whoText(r.requester.split(" ")[0], null, r.requester_region, t) })}</p>
          <p className="num font-display text-xl font-semibold text-pine-strong" data-testid="request-amount">{t("family.requested", { amount: r.amount })}</p>
        </div>
        <span className={`rounded-md px-2.5 py-0.5 text-xs font-bold ${r.status === "pending" ? "bg-saffron-soft text-saffron-ink" : "bg-verified-soft text-verified"}`} data-testid="request-status">{t(`family.status.${r.status}`)}</span>
      </div>
      {!mine && r.status === "pending" ? (
        <div className="flex gap-2">
          <button type="button" data-testid="accept" disabled={busy === r.request_id} onClick={() => void answer(r.request_id, true)} className="btn btn-primary"><Icon name="check" size={18} />{t("family.accept")}</button>
          <button type="button" data-testid="decline" disabled={busy === r.request_id} onClick={() => void answer(r.request_id, false)} className="btn btn-quiet">{t("family.decline")}</button>
        </div>
      ) : null}
    </li>
  );

  return (
    <section aria-labelledby="family-title" className="flex flex-col gap-4">
      <PageTitle id="family-title" eyebrow={t("family.eyebrow")} sub={t("family.note")}>{t("family.title")}</PageTitle>
      <h2 className="h2">{t("family.incoming")}</h2>
      {data && data.incoming.length === 0 ? <EmptyState icon="family" title={t("family.none")} /> : null}
      <ul className="flex flex-col gap-3">{data?.incoming.map((r) => row(r, false))}</ul>
      <h2 className="h2 mt-2">{t("family.outgoing")}</h2>
      {data && data.outgoing.length === 0 ? <EmptyState icon="send" title={t("family.none")} /> : null}
      <ul className="flex flex-col gap-3">{data?.outgoing.map((r) => row(r, true))}</ul>
    </section>
  );
}
