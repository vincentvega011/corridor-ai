"use client";

import { useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { RejectedData, VerificationRequestResponse } from "@/lib/contracts";
import { ZONE_OF } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { Icon } from "@/components/ui/Icon";
import { Timeline, type TimelineStep } from "@/components/ui/Timeline";
import { TrustChip } from "@/components/ui/TrustChip";

/**
 * Why a payment is on hold. The warning and the reasons are the server's words in the user's language; the
 * earliest allowed time is the server's ISO time shown in the user's own time zone with the zone named.
 * The only action is "ask the bank to verify this payee": it adds a request to the bank officer's queue and
 * changes nothing itself (the customer cannot verify anything).
 */
export function HoldCard({ rejected }: { rejected: RejectedData }) {
  const { t, num, fmtTime } = useI18n();
  const { persona } = useSession();
  const [status, setStatus] = useState<"idle" | "sending" | "pending" | "error">("idle");
  const hold = rejected.hold as { until?: string; cap?: string; untrusted?: boolean } | null | undefined;
  const action = rejected.actions?.find((a) => a.kind === "request_verification");
  const zone = (persona && ZONE_OF[persona]) || "UTC";

  async function ask() {
    if (!action?.payee_id) return;
    setStatus("sending");
    try {
      const r = await api<VerificationRequestResponse>(`payees/${encodeURIComponent(action.payee_id)}/verification-request`, { method: "POST", body: {} });
      setStatus(r.status === "pending" ? "pending" : "idle");
    } catch (e) {
      setStatus(e instanceof ApiError && e.status === 409 ? "pending" : "error");
    }
  }

  const capText = hold?.cap ? (hold.untrusted ? t("hold.untrusted") : t("hold.cap", { cap: num(hold.cap) })) : hold?.untrusted ? t("hold.untrusted") : null;
  const steps: TimelineStep[] = [
    { key: "saved", state: "done", icon: "check", title: t("hold.tl.saved") },
    {
      key: "held",
      state: "current",
      icon: "clock-lock",
      title: hold?.until ? <span data-testid="hold-until">{t("hold.until", { time: fmtTime(hold.until, zone) })}</span> : t("hold.tl.held"),
      body: (
        <>
          {t("hold.tl.heldBody")}
          {capText ? <span className="mt-0.5 block font-medium text-ink" data-testid="hold-cap">{capText}</span> : null}
        </>
      ),
    },
    { key: "after", state: "next", icon: "keypad", title: hold?.untrusted ? t("hold.tl.cappedU") : t("hold.tl.capped"), body: hold?.untrusted ? t("hold.tl.cappedUBody") : t("hold.tl.cappedBody") },
  ];

  return (
    <article role="alert" data-testid="hold-card" className="enter overflow-hidden rounded-md border-[1.5px] border-held bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-2 bg-held-soft px-4 py-3">
        <div className="flex items-center gap-2.5">
          <span className="grid h-9 w-9 place-items-center rounded-md bg-held text-on-solid"><Icon name="clock-lock" size={20} /></span>
          <h3 className="font-display text-lg font-semibold leading-tight text-ink">{t("hold.title")}</h3>
        </div>
        <TrustChip kind="held" />
      </div>
      <div className="flex flex-col gap-3 px-4 py-4">
        <p className="flex items-start gap-2 text-[0.9375rem] font-semibold text-ink"><Icon name="shield" size={19} className="mt-0.5 shrink-0 text-held" /><span>{t("hold.scam")}</span></p>
        {hold?.untrusted ? <p className="flex flex-col items-start gap-1 text-sm" data-testid="hold-document-label"><TrustChip kind="document" />{t("hold.documentName")}</p> : null}
        {rejected.reasons && rejected.reasons.length > 0 ? (
          <ul className="list-disc pl-5 text-sm" data-testid="hold-reasons">
            {rejected.reasons.map((r, i) => (
              <li key={i}>{num(r)}</li>
            ))}
          </ul>
        ) : null}
        <Timeline steps={steps} label={t("hold.tl.label")} />
        <p className="text-xs text-muted">{t("hold.note")}</p>
        {action ? (
          <div>
            {status === "pending" ? (
              <p className="flex items-center gap-2 rounded-md bg-verified-soft px-3 py-2 text-sm font-semibold text-verified" data-testid="hold-asked"><Icon name="check-circle" size={18} />{t("hold.asked")}</p>
            ) : (
              <button type="button" data-testid="ask-bank" onClick={() => void ask()} disabled={status === "sending"} className="btn btn-secondary btn-sm">
                <Icon name="bank" size={17} />
                {t("hold.ask")}
              </button>
            )}
            {status === "error" ? <p role="alert" className="mt-1 text-sm text-deny">{t("common.error")}</p> : null}
          </div>
        ) : null}
      </div>
    </article>
  );
}
