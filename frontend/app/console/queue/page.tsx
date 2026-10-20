"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import { oapi } from "@/lib/console/api";
import { useI18n } from "@/lib/i18n";
import type { OfficerVerificationItem, OfficerVerificationQueueResponse } from "@/lib/contracts";
import { Icon } from "@/components/ui/Icon";
import { OutcomeBadge } from "@/components/ui/OutcomeBadge";
import { TrustChip, type TrustKind } from "@/components/ui/TrustChip";

const METHODS = ["callback_independent_number", "in_branch_check", "university_housing_office_list"] as const;
const REFUSAL_CODES = ["cop_mismatch", "cop_no_record", "already_verified", "method_required", "reference_required"];

function VerifyForm({ item, onDone }: { item: OfficerVerificationItem; onDone: (msg: string) => void }) {
  const { tc } = useI18n();
  const [method, setMethod] = useState<string>(METHODS[0]);
  const [reference, setReference] = useState("");
  const [error, setError] = useState<string | null>(null);
  const blocked = item.cop !== "match"; // a mismatch (or no record) cannot be verified

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await oapi(`officer/payees/${encodeURIComponent(item.payee_id)}/verify`, { method: "POST", body: { method, reference } });
      onDone(tc("c.queue.verified", { account: item.account_masked }));
    } catch (err) {
      const code = err instanceof ApiError ? String(err.detail) : "error";
      setError(REFUSAL_CODES.includes(code) ? tc(`c.queue.refusal.${code}`) : tc("c.queue.notVerified", { code }));
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} data-testid="verify-form" className="mt-3 flex flex-col gap-3 rounded-lg border border-line bg-paper p-3">
      <p className={`flex items-center gap-2 text-sm font-semibold ${blocked ? "text-deny" : "text-verified"}`} data-testid="cop-result"><Icon name={blocked ? "octagon-x" : "check-circle"} size={17} />{tc(`c.queue.cop.${item.cop}`)}</p>
      <fieldset disabled={blocked} className="flex flex-col gap-2 disabled:opacity-50">
        <label className="field">{tc("c.queue.method")}
          <select data-testid="verify-method" value={method} onChange={(e) => setMethod(e.target.value)} className="input">
            {METHODS.map((v) => <option key={v} value={v}>{tc(`c.queue.method.${v}`)}</option>)}
          </select>
        </label>
        <label className="field">{tc("c.queue.reference")}
          <input data-testid="verify-reference" value={reference} onChange={(e) => setReference(e.target.value)} maxLength={200} className="input" />
        </label>
        <button type="submit" data-testid="verify-submit" disabled={reference.trim().length < 3} className="btn btn-primary self-start"><Icon name="badge" size={16} />{tc("c.queue.verify")}</button>
      </fieldset>
      {blocked ? <p className="text-sm text-deny" data-testid="verify-blocked">{tc("c.queue.blocked")}</p> : null}
      {error ? <p role="alert" data-testid="verify-error" className="text-sm text-deny">{error}</p> : null}
    </form>
  );
}

export default function QueuePage() {
  const { tc, fmtOfficerTime } = useI18n();
  const [status, setStatus] = useState<"pending" | "all">("pending");
  const [items, setItems] = useState<OfficerVerificationItem[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems((await oapi<OfficerVerificationQueueResponse>(`officer/verification-requests?status=${status}`)).items);
    } catch {
      setItems([]);
    }
  }, [status]);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- load the queue when the filter changes
  useEffect(() => { void load(); }, [load]);

  const kind = (prov: string): TrustKind => (prov === "user_typed" || prov === "user_confirmed" ? "typed" : prov === "system" ? "verified" : "document");
  return (
    <section aria-labelledby="queue-title" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 id="queue-title" className="text-lg font-semibold">{tc("c.queue.title")}</h1>
          <p className="max-w-2xl text-xs text-muted">{tc("c.queue.note")}</p>
        </div>
        <select data-testid="queue-filter" aria-label={tc("c.queue.filter")} value={status} onChange={(e) => setStatus(e.target.value as "pending" | "all")} className="input w-auto">
          <option value="pending">{tc("c.queue.openOpt")}</option>
          <option value="all">{tc("c.queue.allOpt")}</option>
        </select>
      </div>
      {notice ? <p role="status" data-testid="verify-notice" className="enter flex items-center gap-2 rounded-lg border border-verified bg-verified-soft px-3 py-2 text-verified"><Icon name="check-circle" size={17} className="draw" />{notice}</p> : null}
      {items && items.length === 0 ? <div className="empty" data-testid="queue-empty"><Icon name="check-circle" size={22} /><p>{tc("c.queue.empty")}</p></div> : null}
      <ul className="flex flex-col gap-2">
        {items?.map((i) => (
          <li key={i.request_id} data-testid="queue-item" data-cop={i.cop} className={`card p-3 ${i.provenance !== "user_typed" && i.provenance !== "system" ? "zone-untrusted" : "zone-core"}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              {/* names come from customers or documents: shown as plain text */}
              <strong className="text-sm" data-testid="queue-name">{i.payee_name}</strong>
              <span className="mono num text-xs text-muted">{i.account_masked} {i.currency}</span>
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-muted">
              <span>{tc("c.customer")} <b className="mono text-ink">{i.user_id}</b></span>
              <span>{tc("c.queue.origin")}</span>
              <TrustChip kind={kind(i.provenance)} label={`${i.provenance}${i.provenance !== "user_typed" && i.provenance !== "system" ? tc("c.queue.untrusted") : ""}`} />
              <span>{tc(`c.queue.status.${i.status}`)}</span>
              <span className="mono">{fmtOfficerTime(i.created_at)}</span>
              <span className="ml-auto" data-testid="queue-cop">
                <OutcomeBadge level={i.cop === "match" ? "allow" : i.cop === "mismatch" ? "deny" : "mfa"} label={tc("c.queue.copBadge", { cop: i.cop })} />
              </span>
            </div>
            {i.status === "pending" ? (
              open === i.request_id ? (
                <VerifyForm item={i} onDone={(m) => { setNotice(m); setOpen(null); void load(); }} />
              ) : (
                <button type="button" data-testid="queue-open" onClick={() => setOpen(i.request_id)} className="btn btn-secondary btn-sm mt-2">{tc("c.queue.review")}</button>
              )
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
