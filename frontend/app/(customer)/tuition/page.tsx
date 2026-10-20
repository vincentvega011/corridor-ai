"use client";

import { useCallback, useEffect, useState } from "react";
import { ChatPanel } from "@/components/ChatPanel";
import { api } from "@/lib/api";
import type { ContributionsResponse, TuitionPlanResponse } from "@/lib/contracts";
import { accountText, invoiceText, whoText } from "@/lib/display";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { useAgent } from "@/lib/useAgent";
import { Icon } from "@/components/ui/Icon";
import { EmptyState, PageTitle, Skeleton } from "@/components/ui/States";
import { Timeline, type TimelineStep } from "@/components/ui/Timeline";

// The sandbox seeds one tuition invoice for Priya; the invoice list is not part of the frozen API.
const DEMO_INVOICE: Record<string, string> = { usr_priya: "inv_priya_tuition_2026s2", usr_anil: "inv_anil_tuition_2026f" };

export default function TuitionPage() {
  const { t, num, fmtDate, apiLang } = useI18n();
  const { persona, epoch } = useSession();
  const invoice = persona ? DEMO_INVOICE[persona] : undefined;
  const [plan, setPlan] = useState<TuitionPlanResponse | null>(null);
  const [requests, setRequests] = useState<ContributionsResponse | null>(null);
  const [loaded, setLoaded] = useState(false);
  const agent = useAgent();
  const confirmed = agent.items.filter((i) => i.kind === "card" && i.state === "done").length;

  const load = useCallback(async () => {
    if (!persona) return;
    try { setRequests(await api<ContributionsResponse>("contributions")); } catch { setRequests(null); }
    if (invoice) {
      try { setPlan(await api<TuitionPlanResponse>(`tuition/plan?invoice_id=${encodeURIComponent(invoice)}`)); } catch { setPlan(null); }
    } else setPlan(null);
    setLoaded(true);
  }, [persona, invoice]);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- load data on persona change and after each confirmation
  useEffect(() => { void load(); }, [load, epoch, confirmed]);

  const steps: TimelineStep[] =
    plan?.steps?.map((s) => ({
      key: String(s.step),
      state: "current-pine" as const,
      icon: s.type === "request_contribution" ? ("family" as const) : ("bank" as const),
      testId: "plan-step",
      title: (
        <>
          <span className="eyebrow mr-2">{t("tuition.step")} {num(String(s.step))}</span>
          {s.type === "request_contribution" ? t("tuition.stepKind.request") : t("tuition.stepKind.pay")}
        </>
      ),
      body:
        s.type === "request_contribution"
          ? t("tuition.step.request", { who: whoText(s.from_name, s.from_relation, s.from_region, t), amount: s.request ?? "", arrives: s.arrives ?? "", route: s.route ?? "", fee: s.fee ?? "", h: s.eta_hours ?? "" })
          : t("tuition.step.pay", { amount: s.amount ?? "", account: accountText(s.from_account_label ?? s.from_account ?? "", t) }),
    })) ?? [];
  if (plan?.due_date) steps.push({ key: "due", state: "next", icon: "calendar", title: t("tuition.dueStep", { date: fmtDate(plan.due_date) }), body: t("tuition.dueStepBody") });

  return (
    <section aria-labelledby="tuition-title" className="flex flex-col gap-4">
      <PageTitle id="tuition-title" eyebrow={t("tuition.eyebrow")} sub={t("tuition.sub")}>{t("tuition.title")}</PageTitle>
      {!invoice ? <EmptyState icon="tuition" title={t("tuition.noInvoices")} testId="tuition-none-for-persona">{t("tuition.noInvoicesBody")}</EmptyState> : null}
      {invoice && !plan && !loaded ? <Skeleton rows={2} label={t("common.loading")} /> : null}
      {plan?.found && plan.status === "ok" ? (
        <div className="flex flex-col gap-4" data-testid="plan">
          <div className="card card-raised enter flex flex-col gap-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <p className="font-semibold" data-testid="plan-invoice">{plan.invoice_label ? invoiceText(plan.invoice_label, t) : ""}</p>
            </div>
            <div>
              <p className="text-sm font-semibold text-muted">{t("tuition.owed")}</p>
              <p className="num font-display text-3xl font-semibold text-pine-strong">{num(plan.still_owed ?? plan.amount ?? "")}</p>
            </div>
            <dl className="grid grid-cols-1 gap-y-1 text-sm min-[420px]:grid-cols-2 min-[420px]:gap-x-4" data-testid="plan-totals">
              {plan.paid_now ? (<div><dt className="text-muted">{t("tuition.paidNow")}</dt><dd className="num font-semibold">{num(plan.paid_now)}</dd></div>) : null}
              {plan.after_family_arrives ? (<div><dt className="text-muted">{t("tuition.afterFamily")}</dt><dd className="num font-semibold">{num(plan.after_family_arrives)}</dd></div>) : null}
              {plan.total_fees ? (<div><dt className="text-muted">{t("tuition.totalFees")}</dt><dd className="num font-semibold" data-testid="plan-fees">{num(plan.total_fees)}</dd></div>) : null}
            </dl>
            {plan.rate_date ? <p className="text-xs text-muted" data-testid="plan-rate-source">{t("rate.line", { date: fmtDate(plan.rate_date) })}</p> : null}
          </div>
          <div className="card flex flex-col gap-3">
            <h2 className="h2">{t("tuition.sources")}</h2>
            <Timeline steps={steps} label={t("tuition.sources")} />
            <p className="hint flex items-center gap-1.5"><Icon name="shield-check" size={16} className="text-pine" />{t("tuition.confirmEach")}</p>
            <button type="button" data-testid="plan-start" disabled={agent.busy} onClick={() => invoice && void agent.sendAction(t("tuition.startLine"), { kind: "pay_invoice", invoice_id: invoice, language: apiLang })} className="btn btn-primary self-start">
              <Icon name="arrow" size={18} />
              {t("tuition.start")}
            </button>
          </div>
        </div>
      ) : plan ? (
        <EmptyState icon="alert" title={t("tuition.none")} testId="plan-none" />
      ) : null}

      <div data-testid="plan-status" className="card">
        <h2 className="h2 mb-2 flex items-center gap-2"><Icon name="family" size={18} className="text-pine" />{t("tuition.requests")}</h2>
        {requests && requests.outgoing.length > 0 ? (
          <ul className="flex flex-col gap-2 text-sm">
            {requests.outgoing.map((r) => (
              <li key={r.request_id} data-testid="plan-request" className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-paper px-3 py-2">
                <span>{whoText(r.contributor.split(" ")[0], r.contributor_relation, r.contributor_region, t)}: {t("family.requested", { amount: r.amount })}</span>
                <span className="rounded-md bg-sand px-2.5 py-0.5 text-xs font-bold" data-testid="plan-request-status">{t(`family.status.${r.status}`)}</span>
              </li>
            ))}
          </ul>
        ) : <p className="text-sm text-muted">{t("tuition.noRequests")}</p>}
      </div>
      <ChatPanel agent={agent} showInput={false} />
    </section>
  );
}
