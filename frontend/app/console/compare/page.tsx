"use client";

import { useEffect, useState } from "react";
import { oapi } from "@/lib/console/api";
import type { CaseComparisonResponse, CaseListResponse, CaseSide } from "@/lib/contracts";
import { Icon } from "@/components/ui/Icon";
import { useI18n } from "@/lib/i18n";

function Side({ title, sides, testId, tone }: { title: string; sides: CaseSide[]; testId: string; tone: "baseline" | "full" }) {
  const { tc } = useI18n();
  return (
    <div data-testid={testId} className={`min-w-0 flex-1 overflow-hidden rounded-lg border-2 ${tone === "baseline" ? "border-dashed border-doc" : "border-verified"} bg-surface`}>
      <h2 className={`flex items-center gap-2 px-3 py-2 text-sm font-semibold ${tone === "baseline" ? "bg-doc-soft text-doc" : "bg-verified-soft text-verified"}`}>
        <Icon name={tone === "baseline" ? "alert" : "shield-check"} size={17} />{title}
      </h2>
      <div className="flex flex-col gap-3 p-3">
        {sides.length === 0 ? <p className="text-muted">{tc("c.cmp.noResult")}</p> : null}
        {sides.map((s, i) => {
          const moved = !/^NPR 0(\.00)?$/.test(String(s.unauthorized_npr)) && String(s.unauthorized_npr) !== "0";
          return (
            <article key={i} className="border-t border-line pt-3 first:border-0 first:pt-0" data-testid="side-result">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="mono text-xs font-medium">{s.user_model}</span>
                <span data-testid="side-outcome" className={`rounded px-2 py-0.5 text-xs font-bold ${/blocked|contained/.test(String(s.outcome)) ? "bg-verified text-on-solid" : "bg-deny text-on-solid"}`}>{s.outcome}</span>
              </div>
              <p className={`mt-2 rounded-md px-3 py-2 ${moved ? "bg-deny-soft text-deny" : "bg-verified-soft text-verified"}`}>
                <span className="eyebrow block !text-inherit opacity-80">{tc("c.cmp.moved")}</span>
                <span className="num font-display text-xl font-semibold" data-testid="side-moved">{s.unauthorized_npr}</span>
                <span className="ml-2 text-xs">{tc("c.cmp.transfers", { n: s.new_transfers ?? 0 })}</span>
              </p>
              <p className="mt-2 flex flex-wrap items-center gap-1 text-xs"><span className="text-muted">{tc("c.cmp.rules")}</span>{(s.rule_ids ?? []).length ? (s.rule_ids ?? []).map((r) => <span key={r} className="rulechip">{r}</span>) : <span>{tc("c.cmp.none")}</span>}</p>
              {(s.hits ?? []).length ? <p className="mt-1 text-xs text-deny">{tc("c.cmp.effects", { x: (s.hits ?? []).join(", ") })}</p> : null}
              {/* the transcript comes from a model: plain text only */}
              <blockquote className="mt-2 whitespace-pre-wrap rounded-md border border-line bg-paper p-2 text-xs" data-testid="side-reply">{s.final_reply}</blockquote>
            </article>
          );
        })}
      </div>
    </div>
  );
}

export default function ComparePage() {
  const { tc, fmtOfficerTime } = useI18n();
  const [list, setList] = useState<CaseListResponse | null>(null);
  const [pick, setPick] = useState("");
  const [one, setOne] = useState<CaseComparisonResponse | null>(null);
   
  useEffect(() => { void oapi<CaseListResponse>("officer/cases").then((l) => { setList(l); if (l.cases?.some((c) => c.id === "RT-026")) setPick("RT-026"); /* a case where the published baseline moved money and Corridor AI did not */ }, () => setList(null)); }, []);
  useEffect(() => {
     
    if (pick) void oapi<CaseComparisonResponse>(`officer/compare/${encodeURIComponent(pick)}`).then(setOne, () => setOne(null));
  }, [pick]);

  return (
    <section aria-labelledby="cmp-title" className="flex flex-col gap-3">
      <div>
        <h1 id="cmp-title" className="text-lg font-semibold">{tc("c.cmp.title")}</h1>
        {list?.found ? (
          <p className="text-xs text-muted" data-testid="report-id">
            {tc("c.cmp.report", { report: list.report ?? "", finished: fmtOfficerTime(String(list.report_finished)), stamp: list.report_stamp ?? "" })}
          </p>
        ) : <p className="text-muted">{tc("c.noReport")}</p>}
      </div>
      <label className="flex items-center gap-2 text-xs font-medium">{tc("c.cmp.case")}
        <select data-testid="case-select" value={pick} onChange={(e) => setPick(e.target.value)} className="input w-auto min-w-64">
          <option value="">{tc("c.cmp.choose")}</option>
          {list?.cases?.map((c) => <option key={c.id} value={c.id}>{c.id} {c.category ? `(${c.category})` : ""}</option>)}
        </select>
      </label>
      {!one?.found ? <div className="empty"><Icon name="route" size={22} /><p>{tc("c.cmp.empty")}</p></div> : null}
      {one?.found ? (
        <div className="flex flex-col gap-3 lg:flex-row">
          <Side title={tc("c.cmp.baseline")} sides={one.baseline ?? []} testId="side-baseline" tone="baseline" />
          <Side title={tc("c.cmp.full")} sides={one.full ?? []} testId="side-full" tone="full" />
        </div>
      ) : null}
    </section>
  );
}
