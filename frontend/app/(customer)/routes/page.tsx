"use client";

import { useEffect, useState } from "react";
import { ChatPanel } from "@/components/ChatPanel";
import { ApiError, api } from "@/lib/api";
import type { CurrencyCode, PayeesResponse, PayeeView, RouteComparisonResponse, RouteOptionView } from "@/lib/contracts";
import { toMinorUnits, ZONE_OF } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { Icon } from "@/components/ui/Icon";
import { EmptyState, PageTitle } from "@/components/ui/States";
import { useAgent } from "@/lib/useAgent";

const CURRENCIES: CurrencyCode[] = ["AUD", "QAR", "CNY", "MYR", "USD", "NPR"];

function Option({ o, best, rank, saving }: { o: RouteOptionView; best?: boolean; rank: number; saving?: string | null }) {
  const { t, num, fmtTime } = useI18n();
  const { persona } = useSession();
  const zone = (persona && ZONE_OF[persona]) || "UTC";
  return (
    <li
      className={best ? "enter relative overflow-hidden rounded-md border-2 border-pine bg-surface" : "rounded-md border border-line bg-surface"}
      data-testid={best ? "route-recommended" : "route-alt"}
    >
      {best ? (
        <div className="flex flex-wrap items-center justify-between gap-2 bg-pine px-4 py-2 text-on-solid">
          <span className="flex items-center gap-1.5 text-sm font-bold"><Icon name="star" size={16} />{t("routes.recommended")}</span>
          {saving ? <span className="rounded-md bg-saffron px-3 py-0.5 text-sm font-bold text-on-solid" data-testid="routes-saving">{t("routes.saving", { amount: saving })}</span> : null}
        </div>
      ) : null}
      <div className={best ? "p-4" : "px-4 py-3"}>
        <div className="flex items-start justify-between gap-3">
          <p className="flex items-center gap-2 font-semibold" data-testid="route-name">
            {!best ? <span className="grid h-6 w-6 place-items-center rounded-md bg-sand text-xs font-bold text-muted">{num(String(rank))}</span> : null}
            {o.route}
          </p>
        </div>
        <dl className={`mt-2 grid gap-x-4 gap-y-2 grid-cols-2 min-[460px]:grid-cols-3`}>
          <div className="col-span-2 min-[460px]:col-span-1">
            <dt className="text-xs font-semibold text-muted">{t("routes.received")}</dt>
            <dd className={`num font-display ${best ? "text-2xl font-semibold text-pine-strong" : "text-base font-semibold"}`} data-testid="route-received">{num(o.received)}</dd>
          </div>
          <div>
            <dt className="text-xs font-semibold text-muted">{t("routes.cost")}</dt>
            <dd className="num font-semibold" data-testid="route-cost">{num(o.total_cost)}</dd>
          </div>
          <div>
            <dt className="text-xs font-semibold text-muted">{t("routes.arrivesLabel", { h: o.eta_hours })}</dt>
            <dd className="num font-semibold" data-testid="route-arrival">{fmtTime(o.arrives_at ?? o.arrives_by, zone)}</dd>
          </div>
        </dl>
        {o.note ? <p className="mt-2 text-sm text-muted">{o.note}</p> : null}
      </div>
    </li>
  );
}

export default function RoutesPage() {
  const { t, num, fmtDate, apiLang } = useI18n();
  const { persona, epoch } = useSession();
  const agent = useAgent();
  const [payees, setPayees] = useState<PayeeView[]>([]);
  const [payeeId, setPayeeId] = useState("");
  const [from, setFrom] = useState<CurrencyCode>("AUD");
  const [to, setTo] = useState<CurrencyCode>("NPR");
  const [amount, setAmount] = useState("2000");
  const [deadline, setDeadline] = useState("");
  const [result, setResult] = useState<RouteComparisonResponse | null>(null);
  const [error, setError] = useState(false);
  const minor = toMinorUnits(amount);
  const matching = payees.filter((p) => p.currency === to);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- load the payees when the persona changes
  useEffect(() => { if (!persona) { setPayees([]); return; } void api<PayeesResponse>("payees").then((r) => setPayees(r.payees), () => setPayees([])); }, [persona, epoch]);

  async function compare(e: React.FormEvent) {
    e.preventDefault();
    if (!minor) return;
    setError(false);
    try {
      setResult(await api<RouteComparisonResponse>("routes/compare", { method: "POST", body: { from_currency: from, to_currency: to, amount_minor: Number(minor), deadline: deadline || null } }));
    } catch (err) {
      setResult(null);
      setError(!(err instanceof ApiError) || err.status !== 422);
    }
  }

  return (
    <section aria-labelledby="routes-title" className="flex flex-col gap-4">
      <PageTitle id="routes-title" eyebrow={t("routes.eyebrow")} sub={t("routes.sub")}>{t("routes.title")}</PageTitle>
      <form onSubmit={(e) => void compare(e)} className="card card-raised grid grid-cols-2 gap-3">
        <label className="field">{t("routes.from")}
          <select data-testid="routes-from" value={from} onChange={(e) => setFrom(e.target.value as CurrencyCode)} className="input">
            {CURRENCIES.map((c) => <option key={c}>{c}</option>)}
          </select>
        </label>
        <label className="field">{t("routes.to")}
          <select data-testid="routes-to" value={to} onChange={(e) => setTo(e.target.value as CurrencyCode)} className="input">
            {CURRENCIES.map((c) => <option key={c}>{c}</option>)}
          </select>
        </label>
        <label className="field">{t("routes.amount")}
          <input data-testid="routes-amount" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} className="input num text-lg font-semibold" />
        </label>
        <label className="field">{t("routes.deadline")}
          <input data-testid="routes-deadline" type="date" value={deadline} onChange={(e) => setDeadline(e.target.value)} className="input" />
        </label>
        <button type="submit" data-testid="routes-compare" disabled={!persona || !minor || from === to} className="btn btn-primary col-span-2"><Icon name="route" size={18} />{t("routes.compare")}</button>
      </form>
      {error ? <p role="alert" className="font-semibold text-deny">{t("common.error")}</p> : null}
      {!result && !error ? <EmptyState icon="route" title={t("routes.emptyTitle")}>{t("routes.emptyBody")}</EmptyState> : null}
      {result ? (
        <div data-testid="routes-result" className="flex flex-col gap-3">
          {result.amount ? (
            <dl className="card grid grid-cols-2 gap-x-4 gap-y-1 text-sm" data-testid="routes-summary">
              <dt className="text-muted">{t("routes.sending")}</dt>
              <dd className="num text-right font-semibold">{num(result.amount)}</dd>
              {result.mid_rate && result.pair ? (
                <>
                  <dt className="text-muted">{t("routes.rate", { from: result.pair.split("/")[0] ?? "", to: result.pair.split("/")[1] ?? "", rate: result.mid_rate_precise ?? result.mid_rate })}</dt>
                  <dd className="text-right text-xs text-muted" data-testid="routes-rate">{result.rate_date ? t("routes.rateSource", { date: fmtDate(result.rate_date) }) : null}</dd>
                </>
              ) : null}
            </dl>
          ) : null}
          {result.status === "ok" && result.recommended ? (
            <ul className="flex flex-col gap-3">
              <Option o={result.recommended} best rank={1} saving={result.saving_vs_most_expensive} />
              {result.split_tip ? <li className="flex items-start gap-2 rounded-md border border border-line-strong bg-sand p-3 text-sm" data-testid="routes-tip"><Icon name="info" size={18} className="mt-0.5 shrink-0 text-saffron-ink" /><span>{t("routes.tipLine", { route: result.split_tip.route, cost: result.split_tip.total_cost, extra: result.split_tip.extra_saving ?? "" })}</span></li> : null}
              {result.alternatives && result.alternatives.length > 0 ? <li className="eyebrow mt-1">{t("routes.alternatives")}</li> : null}
              {result.alternatives?.map((o, i) => <Option key={o.route} o={o} rank={i + 2} />)}
            </ul>
          ) : (
            <EmptyState icon="alert" title={t("routes.noRoute")} testId="routes-none">{result.note ?? ""}</EmptyState>
          )}
        </div>
      ) : null}
      {result && result.status === "ok" && result.recommended ? (
        <div className="card flex flex-col gap-2" data-testid="routes-send">
          <h2 className="h2">{t("routes.sendTitle")}</h2>
          {matching.length === 0 ? <p className="text-sm text-muted">{t("routes.noPayee", { ccy: to })}</p> : (
            <form
              className="flex flex-wrap items-end gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                const p = matching.find((x) => x.payee_id === (payeeId || matching[0]?.payee_id));
                const send = minor && result.pair ? minor : null;
                if (!p || !send) return;
                // the amount in the form is what is sent: the server prices the route again and shows the card
                void agent.sendAction(t("routes.sendLine", { amount: num(result.amount ?? ""), name: p.display_name }), { kind: "send_money", payee_id: p.payee_id, amount_minor: Number(send), currency: from, language: apiLang });
              }}
            >
              <label className="field min-w-[12rem] flex-1">{t("routes.payee")}
                <select data-testid="routes-send-payee" value={payeeId || matching[0]?.payee_id} onChange={(e) => setPayeeId(e.target.value)} className="input">
                  {matching.map((p) => <option key={p.payee_id} value={p.payee_id}>{p.display_name}</option>)}
                </select>
              </label>
              <button type="submit" data-testid="routes-send-button" disabled={agent.busy} className="btn btn-primary"><Icon name="send" size={18} />{t("routes.sendButton")}</button>
            </form>
          )}
        </div>
      ) : null}
      <ChatPanel agent={agent} showInput={false} />
    </section>
  );
}
