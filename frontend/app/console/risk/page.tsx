"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { oapi } from "@/lib/console/api";
import type { RiskQueueItem, RiskQueueResponse } from "@/lib/contracts";
import { Icon } from "@/components/ui/Icon";
import { useI18n } from "@/lib/i18n";
import { OutcomeBadge, outcomeOf } from "@/components/ui/OutcomeBadge";

export default function RiskPage() {
  const { tc, fmtOfficerTime } = useI18n();
  const [items, setItems] = useState<RiskQueueItem[] | null>(null);
   
  useEffect(() => { void oapi<RiskQueueResponse>("officer/risk/queue?limit=100").then((r) => setItems(r.items), () => setItems([])); }, []);
  const sorted = [...(items ?? [])].sort((a, b) => (b.risk?.score_bps ?? 0) - (a.risk?.score_bps ?? 0));
  return (
    <section aria-labelledby="risk-title" className="flex flex-col gap-3">
      <div>
        <h1 id="risk-title" className="text-lg font-semibold">{tc("c.risk.title")}</h1>
        <p className="max-w-3xl text-xs text-muted">{tc("c.risk.note")}</p>
      </div>
      {items && items.length === 0 ? <div className="empty" data-testid="risk-empty"><Icon name="check-circle" size={22} /><p>{tc("c.risk.empty")}</p></div> : null}
      {items && items.length > 0 ? <div className="card overflow-x-auto p-0">
        <table className="ctable">
          <thead><tr><th>{tc("c.time")}</th><th>{tc("c.risk.score")}</th><th>{tc("c.risk.level")}</th><th>{tc("c.risk.decision")}</th><th>{tc("c.customer")}</th><th>{tc("c.risk.action")}</th><th>{tc("c.risk.reasons")}</th><th>{tc("c.risk.trace")}</th></tr></thead>
          <tbody>
            {sorted.map((i) => (
              <tr key={i.seq} data-testid="risk-row">
                <td className="mono text-xs">{fmtOfficerTime(i.ts)}</td>
                <td className="mono num text-right font-semibold" data-testid="risk-score">{i.risk ? i.risk.score_bps : "-"}</td>
                <td>{i.risk?.level ?? "-"}</td>
                <td><span className="inline-flex items-center gap-1.5"><OutcomeBadge level={outcomeOf(i.decision)} label={i.decision} />{i.executed ? <span className="text-xs text-muted">{tc("c.risk.executed")}</span> : null}</span></td>
                <td className="mono">{i.user_id}</td>
                <td>{i.action}</td>
                <td><span className="flex flex-wrap gap-1">{(i.risk?.reasons.length ? i.risk.reasons.map((r) => `${r.code} +${r.contribution_bps}`) : i.rule_ids).map((r) => <span key={r} className="rulechip">{r}</span>)}</span></td>
                <td><Link className="font-medium text-pine underline" href={`/console/trace?hash=${i.proposal_hash}`}>{tc("c.risk.open")}</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div> : null}
    </section>
  );
}
