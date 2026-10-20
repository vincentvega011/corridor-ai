"use client";

import { useCallback, useEffect, useState } from "react";
import { oapi } from "@/lib/console/api";
import type { ChainStatus, LedgerViewResponse } from "@/lib/contracts";
import { Icon } from "@/components/ui/Icon";
import { formatMinor } from "@/lib/format";
import { useI18n } from "@/lib/i18n";

export default function LedgerPage() {
  const { tc, fmtOfficerTime } = useI18n();
  const [user, setUser] = useState("");
  const [proposal, setProposal] = useState("");
  const [data, setData] = useState<LedgerViewResponse | null>(null);
  const [chain, setChain] = useState<ChainStatus | null>(null);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    setError(false);
    const q = new URLSearchParams({ limit: "100" });
    if (user.trim()) q.set("user", user.trim());
    if (/^[0-9a-f]{64}$/.test(proposal.trim())) q.set("proposal_hash", proposal.trim());
    try {
      setData(await oapi<LedgerViewResponse>(`officer/ledger?${q}`));
    } catch {
      setData(null);
      setError(true);
    }
  }, [user, proposal]);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- first load
  useEffect(() => { void load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function verify() {
    setChain(await oapi<ChainStatus>("officer/ledger/verify"));
  }

  return (
    <section aria-labelledby="ledger-title" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 id="ledger-title" className="text-lg font-semibold">{tc("c.ledger.title")}</h1>
          <p className="text-xs text-muted">{tc("c.ledger.note")}</p>
        </div>
        <button type="button" data-testid="verify-chain" onClick={() => void verify()} className="btn btn-primary"><Icon name="shield-check" size={16} />{tc("c.ledger.verify")}</button>
      </div>
      {chain ? (
        <p role="status" data-testid="chain-result" data-valid={chain.valid ? "yes" : "no"} className={`enter flex items-center gap-3 rounded-lg border-2 px-4 py-3 text-sm font-semibold ${chain.valid ? "border-verified bg-verified-soft text-verified" : "border-deny bg-deny-soft text-deny"}`}>
          <span className={`pop grid h-8 w-8 shrink-0 place-items-center rounded-md ${chain.valid ? "bg-verified text-on-solid" : "bg-deny text-on-solid"}`}><Icon name={chain.valid ? "check" : "octagon-x"} size={18} className={chain.valid ? "draw" : ""} /></span>
          <span>{chain.valid
            ? tc("c.ledger.pass", { n: chain.entries_checked })
            : tc("c.ledger.fail", { seq: String(chain.first_broken_seq), reason: chain.reason ?? tc("c.ledger.mismatch") })}</span>
        </p>
      ) : null}
      <div className="card flex flex-wrap items-end gap-3 p-3">
        <label className="field text-xs">{tc("c.customer")}
          <input data-testid="ledger-user" value={user} onChange={(e) => setUser(e.target.value)} placeholder="usr_priya" className="input mono" />
        </label>
        <label className="field text-xs">{tc("c.ledger.proposalHash")}
          <input data-testid="ledger-proposal" value={proposal} onChange={(e) => setProposal(e.target.value)} className="input mono w-80 text-xs" />
        </label>
        <button type="button" data-testid="ledger-filter" onClick={() => void load()} className="btn btn-quiet">{tc("c.ledger.filter")}</button>
      </div>
      {error ? <p role="alert" className="text-deny">{tc("c.ledger.error")}</p> : null}
      <div className="card overflow-x-auto p-0">
        <table className="ctable table-fixed" data-testid="ledger-table">
          <thead><tr><th className="w-14">{tc("c.ledger.seq")}</th><th className="w-44">{tc("c.time")}</th><th className="w-40">{tc("c.ledger.event")}</th><th className="w-20">{tc("c.ledger.actor")}</th><th className="w-28">{tc("c.customer")}</th><th>{tc("c.ledger.entryHash")}</th></tr></thead>
          <tbody>
            {data?.entries.map((e) => (
              <tr key={e.seq} data-testid="ledger-row">
                <td className="mono text-muted">{e.seq}</td>
                <td className="mono text-xs">{fmtOfficerTime(e.ts)}</td>
                <td><span className="rulechip" data-result={/reject|denied|failed|lock/.test(e.event_type) ? "deny" : /execution|granted/.test(e.event_type) ? "allow" : ""}>{e.event_type}</span></td>
                <td>{e.actor}</td>
                <td className="mono">{String(e.payload.user_id ?? "")}</td>
                <td className="mono break-all text-xs">
                  {e.entry_hash.slice(0, 20)}
                  <details><summary className="cursor-pointer text-pine">{tc("c.ledger.payload")}{e.payload.amount_minor ? `: ${formatMinor(e.payload.amount_minor, e.payload.currency)}` : ""}</summary><pre className="whitespace-pre-wrap break-all text-muted">{JSON.stringify(e.payload, null, 1)}</pre></details>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
