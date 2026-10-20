"use client";

import { useCallback, useEffect, useState } from "react";
import { ChatPanel } from "@/components/ChatPanel";
import { api } from "@/lib/api";
import type { PayeesResponse, PayeeView } from "@/lib/contracts";
import { toMinorUnits, ZONE_OF } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { useAgent } from "@/lib/useAgent";
import { Icon } from "@/components/ui/Icon";
import { EmptyState, PageTitle, Skeleton } from "@/components/ui/States";
import { TrustChip, trustOfProvenance } from "@/components/ui/TrustChip";

function PayeeRow({ p, onPay, busy }: { p: PayeeView; onPay: (p: PayeeView, minor: string) => void; busy: boolean }) {
  const { t, num, fmtTime } = useI18n();
  const { persona } = useSession();
  const zone = (persona && ZONE_OF[persona]) || "UTC";
  const [asked, setAsked] = useState(false);
  const [amount, setAmount] = useState("");
  const minor = toMinorUnits(amount);
  const canAsk = !p.verified && p.verification !== "pending" && !asked;
  const untrusted = p.name_untrusted || p.provenance === "untrusted";
  return (
    <li data-testid="payee-row" data-payee={p.payee_id} className={`enter card flex flex-col gap-2.5 ${untrusted ? "zone-doc border-dashed" : ""}`}>
      <div className="flex items-start gap-3">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-md bg-pine-soft font-display text-lg font-semibold text-pine-strong" aria-hidden="true">{(p.display_name.trim()[0] ?? "?").toUpperCase()}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3">
            {/* a payee made from a document shows the bank's fixed label, never the document's own words */}
            <span className="min-w-0"><strong className="break-words" data-testid="payee-name">{p.display_name}</strong>
            {p.added_via === "seed" ? <span data-testid="synthetic-badge" className="ml-2 rounded-md bg-sand px-2 py-0.5 text-xs font-normal text-muted">{t("badge.synthetic")}</span> : null}</span>
            <span className="mono num text-sm text-muted">{num(p.account_no)}</span>
          </div>
          <p className="mt-1 flex flex-wrap items-center gap-1.5" data-testid="payee-prov-wrap">
            <span data-testid="payee-prov"><TrustChip kind={trustOfProvenance(p.provenance, p.verified)} label={t(`payees.prov.${p.provenance}`)} /></span>
          </p>
          {p.name_untrusted ? <p className="mt-1 text-sm text-doc" data-testid="payee-doc-label">{t("payees.docLabel")}</p> : null}
        </div>
      </div>
      <p className="flex flex-wrap items-center gap-1.5 text-sm" data-testid="payee-status">
        {p.verified ? <TrustChip kind="verified" label={t("payees.verified")} /> : <span className="rounded-md bg-sand px-2.5 py-0.5 text-xs font-semibold text-muted">{t("payees.unverified")}</span>}
        {p.hold_until ? <TrustChip kind="held" label={t("payees.hold", { time: fmtTime(p.hold_until, zone) })} /> : <span className="text-muted">{t("payees.noHold")}</span>}
        {p.verification === "pending" || asked ? <span className="font-semibold text-held">{t("hold.pending")}</span> : null}
        {p.verification === "refused" ? <span className="font-semibold text-deny">{t("hold.refused")}</span> : null}
      </p>
      <hr className="rule" />
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => { e.preventDefault(); if (minor) { onPay(p, minor); setAmount(""); } }}
      >
        <label className="field w-36">{t("pay.amount", { ccy: p.currency })}
          <input data-testid="payee-pay-amount" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} className="input num" />
        </label>
        <button type="submit" data-testid="payee-pay" disabled={!minor || busy} className="btn btn-primary btn-sm"><Icon name="send" size={16} />{t("pay.button")}</button>
        {canAsk ? (
          <button
            type="button"
            data-testid="payee-ask-bank"
            className="btn btn-secondary btn-sm"
            onClick={async () => {
              await api(`payees/${encodeURIComponent(p.payee_id)}/verification-request`, { method: "POST", body: {} });
              setAsked(true);
            }}
          >
            <Icon name="bank" size={16} />
            {t("hold.ask")}
          </button>
        ) : null}
      </form>
    </li>
  );
}

export default function PayeesPage() {
  const { t, apiLang } = useI18n();
  const { persona, epoch } = useSession();
  const [payees, setPayees] = useState<PayeeView[] | null>(null);
  const [name, setName] = useState("");
  const [account, setAccount] = useState("");
  const agent = useAgent();

  const load = useCallback(async () => {
    if (!persona) { setPayees([]); return; }
    try {
      setPayees((await api<PayeesResponse>("payees")).payees);
    } catch {
      setPayees([]);
    }
  }, [persona]);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- loading data when the persona changes
  useEffect(() => { void load(); }, [load, epoch]);
  // after a card is confirmed the list refreshes
  const doneCount = agent.items.filter((i) => i.kind === "card" && i.state === "done").length;
  // eslint-disable-next-line react-hooks/set-state-in-effect -- reload after a confirmation
  useEffect(() => { if (doneCount) void load(); }, [doneCount, load]);

  return (
    <section aria-labelledby="payees-title" className="flex flex-col gap-4">
      <PageTitle id="payees-title" eyebrow={t("payees.eyebrow")} sub={t("payees.sub")}>{t("payees.title")}</PageTitle>
      {payees === null ? <Skeleton rows={2} label={t("common.loading")} /> : null}
      {payees && payees.length === 0 ? <EmptyState icon="payees" title={t("payees.empty")}>{t("payees.emptyBody")}</EmptyState> : null}
      <ul className="flex flex-col gap-3">{payees?.map((p) => <PayeeRow key={p.payee_id} p={p} busy={agent.busy} onPay={(x, minor) => void agent.sendAction(t("pay.line", { amount: `${x.currency} ${(Number(minor) / 100).toFixed(2)}`, name: x.display_name }), { kind: "send_money", payee_id: x.payee_id, amount_minor: Number(minor), currency: x.currency, language: apiLang })} />)}</ul>

      <form
        className="card card-raised flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          // the user's own words: the server finds the number in them and decides where it came from
          void agent.send(`Add recipient ${name.trim()}, account ${account.trim()}`);
          setAccount("");
        }}
      >
        <h2 className="h2 flex items-center gap-2"><Icon name="plus" size={18} className="text-pine" />{t("payees.add")}</h2>
        <label className="field">
          {t("payees.name")}
          <input data-testid="payee-name-input" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} className="input" required />
        </label>
        <label className="field">
          {t("payees.account")}
          <input data-testid="payee-account-input" inputMode="numeric" value={account} onChange={(e) => setAccount(e.target.value.replace(/[^\d -]/g, ""))} maxLength={24} className="input mono" required />
        </label>
        <p className="hint flex items-start gap-1.5"><TrustChip kind="typed" />{t("payees.saveNote")}</p>
        <button type="submit" data-testid="payee-save" disabled={!persona || agent.busy || !name.trim() || account.replace(/\D/g, "").length < 6} className="btn btn-primary self-start">
          {t("payees.save")}
        </button>
      </form>
      <ChatPanel agent={agent} showInput={false} />
    </section>
  );
}
