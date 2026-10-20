"use client";

import { useEffect, useState } from "react";
import { oapi } from "@/lib/console/api";
import type { EvalSummaryResponse } from "@/lib/contracts";
import { Icon } from "@/components/ui/Icon";
import { useI18n } from "@/lib/i18n";

export default function EvalPage() {
  const { tc, fmtOfficerTime } = useI18n();
  const [data, setData] = useState<EvalSummaryResponse | null>(null);
   
  useEffect(() => { void oapi<EvalSummaryResponse>("officer/eval/summary").then(setData, () => setData(null)); }, []);
  if (!data) return <div role="status" className="flex flex-col gap-2"><div className="skeleton h-5 w-48" /><div className="skeleton h-32 w-full" /></div>;
  if (!data.found) return <p data-testid="eval-none">{tc("c.noReport")}</p>;
  const preview = (data.stamp ?? "").toLowerCase().includes("preview");
  return (
    <section aria-labelledby="eval-title" className="flex flex-col gap-3">
      <h1 id="eval-title" className="text-lg font-semibold">{tc("c.eval.title")}</h1>
      <p className="text-xs" data-testid="eval-report">{tc("c.eval.report")} <strong className="mono">{data.report}</strong> | {tc("c.eval.finished")} {fmtOfficerTime(String(data.finished))} | {data.stamp}</p>
      {preview ? <p className="card flex items-center gap-2 p-3 text-xs" data-testid="eval-preview"><Icon name="info" size={16} className="shrink-0 text-muted" />{tc("c.eval.preview")}</p> : null}
      <div className="card overflow-x-auto p-0">
        <table className="ctable" data-testid="eval-table">
          <thead>
            <tr><th>{tc("c.eval.userModel")}</th><th>{tc("c.eval.system")}</th><th className="text-right">{tc("c.eval.blocked")}</th><th className="text-right">{tc("c.eval.contained")}</th><th className="text-right">{tc("c.eval.harmful")}</th><th className="text-right">{tc("c.eval.moved")}</th><th className="text-right">{tc("c.eval.completion")}</th><th className="text-right">{tc("c.eval.falseBlock")}</th></tr>
          </thead>
          <tbody>
            {(data.headline ?? []).map((r) => (
              <tr key={`${r.user_model}-${r.system}`} data-testid="eval-row" className={r.system === "full" ? "bg-verified-soft/30" : ""}>
                <td className="mono">{r.user_model}</td><td className="font-semibold">{r.system}</td>
                <td className="num text-right">{r.blocked}</td><td className="num text-right">{r.contained}</td><td className="num text-right">{r.harmful}</td>
                <td className="num text-right font-semibold" data-testid="eval-moved">{r.npr_moved}</td><td className="num text-right">{r.task_completion}</td><td className="num text-right">{r.false_block}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted">{tc("c.eval.foot")}</p>
    </section>
  );
}
