"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { PassportChallengeResponse, PassportVerifyResponse } from "@/lib/contracts";
import { useI18n } from "@/lib/i18n";
import { Icon } from "@/components/ui/Icon";
import { PageTitle } from "@/components/ui/States";
import { TrustChip } from "@/components/ui/TrustChip";

/** The verifier's page (a landlord or lender): public, no login. It issues the challenge and checks the answer. */
export default function VerifyPage() {
  const { t } = useI18n();
  const [audience, setAudience] = useState("");
  const [challenge, setChallenge] = useState<PassportChallengeResponse | null>(null);
  const [pasted, setPasted] = useState("");
  const [result, setResult] = useState<PassportVerifyResponse | null>(null);
  const [parseError, setParseError] = useState(false);

  async function makeChallenge(e: React.FormEvent) {
    e.preventDefault();
    setChallenge(await api<PassportChallengeResponse>("passport/challenge", { method: "POST", body: { audience: audience.trim() } }));
    setResult(null);
  }

  async function check(e: React.FormEvent) {
    e.preventDefault();
    setParseError(false);
    let presentation: unknown;
    try {
      presentation = JSON.parse(pasted);
    } catch {
      setParseError(true);
      return;
    }
    setResult(await api<PassportVerifyResponse>("passport/verify", { method: "POST", body: { presentation, expected_audience: audience.trim() } }));
  }

  const reasonText = (r: string | null | undefined) => (r && t(`verify.reason.${r}`) !== `verify.reason.${r}` ? t(`verify.reason.${r}`) : (r ?? ""));

  return (
    <section aria-labelledby="verify-title" className="flex flex-col gap-4">
      <PageTitle id="verify-title" eyebrow={t("verify.eyebrow")} sub={t("verify.sub")}>{t("verify.title")}</PageTitle>
      <form onSubmit={(e) => void makeChallenge(e)} className="card card-raised flex flex-col gap-3">
        <h2 className="h2 flex items-center gap-2"><span className="grid h-6 w-6 place-items-center rounded-md bg-pine text-xs font-bold text-on-solid">1</span>{t("verify.step1")}</h2>
        <label className="field">{t("verify.audience")}
          <input data-testid="verify-audience" value={audience} onChange={(e) => setAudience(e.target.value)} maxLength={60} required className="input" />
        </label>
        <button type="submit" data-testid="verify-challenge" disabled={!audience.trim()} className="btn btn-primary self-start">{t("verify.challenge")}</button>
        {challenge ? (
          <div className="enter rounded-md border border-dashed border-pine bg-pine-soft p-3 text-sm">
            <p className="eyebrow">{t("verify.nonce")}</p>
            <code data-testid="verify-nonce" className="mono mt-1 block break-all text-base font-medium">{challenge.nonce}</code>
            <p className="mt-1 text-muted">{t("verify.expires")}</p>
          </div>
        ) : null}
      </form>

      <form onSubmit={(e) => void check(e)} className="card card-raised flex flex-col gap-3">
        <h2 className="h2 flex items-center gap-2"><span className="grid h-6 w-6 place-items-center rounded-md bg-pine text-xs font-bold text-on-solid">2</span>{t("verify.step2")}</h2>
        <label className="field">{t("verify.paste")}
          <textarea data-testid="verify-input" rows={5} value={pasted} onChange={(e) => setPasted(e.target.value)} className="input mono text-xs" />
        </label>
        <button type="submit" data-testid="verify-check" disabled={!pasted.trim() || !audience.trim()} className="btn btn-primary self-start"><Icon name="badge" size={18} />{t("verify.check")}</button>
        {parseError ? <p role="alert" className="font-semibold text-deny">{t("verify.reason.malformed")}</p> : null}
      </form>

      {result ? (
        <div role="status" data-testid="verify-result" data-valid={result.valid ? "yes" : "no"} className={`enter overflow-hidden rounded-md border-2 ${result.valid ? "border-verified bg-verified-soft" : "border-deny bg-deny-soft"}`}>
          <div className="flex items-center gap-3 px-4 py-3">
            <span className={`pop grid h-10 w-10 shrink-0 place-items-center rounded-md text-on-solid ${result.valid ? "bg-verified" : "bg-deny"}`}><Icon name={result.valid ? "check" : "octagon-x"} size={22} className={result.valid ? "draw" : ""} /></span>
            <p className="font-semibold">{result.valid ? t("verify.valid") : t("verify.invalid", { reason: reasonText(result.reason) })}</p>
          </div>
          {result.valid && result.claims && result.claims.length > 0 ? (
            <div className="border-t border-verified/30 bg-surface/70 px-4 py-3">
              <p className="eyebrow mb-1.5 flex items-center gap-1.5"><TrustChip kind="verified" label={t("verify.claims")} /></p>
              <ul className="list-disc pl-5 text-sm">
                {result.claims.map((c, i) => (
                  <li key={i}>{String((c as { shows?: unknown; statement?: unknown }).shows ?? (c as { statement?: unknown }).statement ?? (c as { id?: unknown }).id)}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
