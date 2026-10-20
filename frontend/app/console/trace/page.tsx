"use client";

import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { buildTrace, ZoneLegend } from "@/components/console/TraceGraph";
import { Icon } from "@/components/ui/Icon";
import { useI18n } from "@/lib/i18n";
import { oapi } from "@/lib/console/api";
import type { LedgerViewResponse, ProposalTraceResponse } from "@/lib/contracts";

// React Flow draws with inline styles, so it is rendered only in the browser (never in server markup).
const TraceGraph = dynamic(() => import("@/components/console/TraceGraph"), { ssr: false });

function Inner() {
  const { tc, fmtOfficerTime } = useI18n();
  const params = useSearchParams();
  const [proposals, setProposals] = useState<LedgerViewResponse["entries"]>([]);
  const [hash, setHash] = useState(params.get("hash") ?? "");
  const [trace, setTrace] = useState<ProposalTraceResponse | null>(null);

   
  useEffect(() => { void oapi<LedgerViewResponse>("officer/ledger?event_type=proposal&limit=50").then((r) => setProposals(r.entries), () => setProposals([])); }, []);
  useEffect(() => {
     
    if (/^[0-9a-f]{64}$/.test(hash)) void oapi<ProposalTraceResponse>(`officer/proposals/${hash}/trace`).then(setTrace, () => setTrace(null));
  }, [hash]);

  const steps = trace?.found ? buildTrace(trace, { tc, time: fmtOfficerTime }).steps : [];
  return (
    <section aria-labelledby="trace-title" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 id="trace-title" className="text-lg font-semibold">{tc("c.trace.title")}</h1>
          <p className="text-xs text-muted">{tc("c.trace.sub")}</p>
        </div>
        <label className="flex items-center gap-2 text-xs font-medium">{tc("c.trace.proposal")}
          <select data-testid="trace-select" value={hash} onChange={(e) => setHash(e.target.value)} className="input max-w-xl">
            <option value="">{tc("c.trace.choose")}</option>
            {proposals.map((p) => (
              <option key={p.seq} value={String(p.payload.proposal_hash)}>
                #{p.seq} {String(p.payload.user_id)} {String(p.payload.action)} {String(p.payload.proposal_hash).slice(0, 10)}
              </option>
            ))}
          </select>
        </label>
      </div>
      {!trace?.found ? <div className="empty"><Icon name="route" size={22} /><p>{tc("c.trace.empty")}</p></div> : null}
      {trace?.found ? (
        <>
          <div className="card flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
            <span>{tc("c.customer")} <b className="mono">{trace.user_id}</b></span>
            <span>{tc("c.trace.hash")} <span className="mono" data-testid="trace-hash">{trace.proposal_hash}</span></span>
            <span className={`ml-auto inline-flex items-center gap-1 font-semibold ${trace.chain?.valid ? "text-verified" : "text-deny"}`}><Icon name={trace.chain?.valid ? "check-circle" : "octagon-x"} size={14} />{tc("c.trace.chain")} {trace.chain?.valid ? tc("c.trace.valid") : tc("c.trace.broken")}</span>
          </div>
          <ZoneLegend />
          <TraceGraph trace={trace} />
          <details className="card p-0" data-testid="trace-steps-toggle"><summary className="cursor-pointer px-3 py-2 text-sm font-semibold text-pine">{tc("c.trace.showSteps")}</summary>
          <ol className="flex list-none flex-col gap-0 p-0 text-xs" data-testid="trace-steps">
            {steps.map((s, i) => (
              <li key={s.id} data-tone={s.tone} data-zone={s.zone} data-testid="trace-step" className={`flex gap-3 border-b border-line px-3 py-2 last:border-0 zone-${s.zone} ${s.tone === "untrusted" ? "font-semibold text-doc" : ""}`}>
                <span className="mono w-5 shrink-0 text-muted">{i + 1}</span>
                <span className="min-w-0 break-words">{s.title}: {s.lines.join(" | ")}{s.at ? <span className="mono ml-2 text-muted">{fmtOfficerTime(s.at)}</span> : null}
                  {s.raw ? <details className="mt-1"><summary className="cursor-pointer text-pine">{tc("c.tr.showParser")}</summary><span className="mono block break-all text-muted">{s.raw}</span></details> : null}
                </span>
              </li>
            ))}
          </ol></details>
        </>
      ) : null}
    </section>
  );
}

export default function TracePage() {
  return <Suspense fallback={null}><Inner /></Suspense>;
}
