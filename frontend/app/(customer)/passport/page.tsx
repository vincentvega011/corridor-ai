"use client";

import { useCallback, useEffect, useState } from "react";
import { ProposalCard } from "@/components/ProposalCard";
import { ApiError, api } from "@/lib/api";
import type { ConfirmRequestData, PassportConfirmResponse, PassportListResponse, PassportProposalResponse } from "@/lib/contracts";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { BrandMark } from "@/components/ui/Brand";
import { Icon } from "@/components/ui/Icon";
import { EmptyState, PageTitle, Skeleton } from "@/components/ui/States";
import { TrustChip } from "@/components/ui/TrustChip";

interface Pending { proposal: PassportProposalResponse; shownAt: number; state: "open" | "sent" | "cancelled" | "done"; claimIds: string[]; audience: string }

function asCard(p: PassportProposalResponse): ConfirmRequestData {
  return {
    hash: p.card.hash, proposal_hash: p.proposal_hash, hashed_fields: p.card.hashed_fields,
    display: p.card.display, route: null, warnings: p.card.warnings ?? [], required_level: p.required_level,
    policy_reasons: p.reasons, rule_ids: p.rule_ids, prompt: "",
  };
}

export default function PassportPage() {
  const { t, fmtDate } = useI18n();
  const { persona, epoch } = useSession();
  const [list, setList] = useState<PassportListResponse | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [presentation, setPresentation] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [audience, setAudience] = useState("");
  const [challenge, setChallenge] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!persona) { setList(null); return; }
    try { setList(await api<PassportListResponse>("passport")); } catch { setList(null); }
  }, [persona]);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- load data when the persona changes
  useEffect(() => { void load(); }, [load, epoch]);

  const latest = list?.passports[0];
  const claimText = (c: { id: string; shows: string; facts?: Record<string, number> }) => {
    const key = `claim.${c.id}`;
    const f = c.facts ?? {};
    const word = (unit: string, n: number | undefined) => t(`unit.${unit}.${n === 1 ? "one" : "other"}`);
    const params = { ...f, monthWord: word("month", f.months), transferWord: word("transfer", f.payments) };
    return t(key, params) === key ? c.shows : t(key, params);
  };

  async function propose(path: string, body: unknown, claimIds: string[] = [], aud = "") {
    setError(null);
    setReceipt(null);
    setPresentation(null);
    try {
      const p = await api<PassportProposalResponse>(path, { method: "POST", body });
      setPending({ proposal: p, shownAt: Date.now(), state: "open", claimIds, audience: aud });
    } catch (e) {
      setPending(null);
      const detail = e instanceof ApiError ? (e.detail as { reason?: string } | string | null) : null;
      setError(typeof detail === "object" && detail?.reason ? detail.reason : "error");
    }
  }

  async function confirm(totp: string | null) {
    if (!pending) return;
    setPending({ ...pending, state: "sent" });
    try {
      const r = await api<PassportConfirmResponse>("passport/confirm", { method: "POST", body: { proposal_hash: pending.proposal.proposal_hash, totp: totp || undefined } });
      setPending({ ...pending, state: "done" });
      setReceipt(
        pending.claimIds.length > 0
          ? t("passport.receiptShared", { audience: pending.audience, claims: (list?.passports[0]?.claims ?? []).filter((c) => pending.claimIds.includes(c.id)).map(claimText).join("; ") })
          : t("passport.receiptIssued"),
      );
      if (r.presentation) setPresentation(JSON.stringify(r.presentation));
      await load();
    } catch {
      setPending({ ...pending, state: "open" });
      setError("error");
    }
  }

  const claims = latest?.claims ?? [];
  const hiddenCount = claims.filter((c) => !picked.includes(c.id)).length;

  return (
    <section aria-labelledby="passport-title" className="flex flex-col gap-4">
      <PageTitle id="passport-title" eyebrow={t("passport.eyebrow")} sub={t("passport.note")}>{t("passport.title")}</PageTitle>

      {persona && !list ? <Skeleton rows={2} label={t("common.loading")} /> : null}
      {list && list.passports.length === 0 ? (
        <EmptyState icon="passport" title={t("passport.none")} testId="passport-empty">
          <button type="button" data-testid="passport-issue" onClick={() => void propose("passport/issue", {})} className="btn btn-primary mt-1"><Icon name="passport" size={18} />{t("passport.issue")}</button>
        </EmptyState>
      ) : null}

      {latest ? (
        <div className="flex flex-col gap-4" data-testid="passport-held">
          <div className="credential enter">
            <div className="flex items-center justify-between gap-3">
              <span className="flex items-center gap-2.5"><BrandMark size={30} /><span className="font-display text-lg font-semibold">{t("passport.title")}</span></span>
              <TrustChip kind="verified" label={t("passport.signed")} />
            </div>
            <p className="mt-6 text-sm opacity-80">{t("passport.expires", { date: fmtDate(latest.expires_at) })}</p>
            <p className="mt-1 text-sm opacity-80">{t("passport.claimsCount", { n: claims.length })}</p>
          </div>

          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              void propose("passport/present", { passport_id: latest.passport_id, claims: picked, audience, challenge: challenge || undefined }, picked, audience);
            }}
          >
            <fieldset className="card flex flex-col gap-2">
              <legend className="h2 mb-1 px-0">{t("passport.choose")}</legend>
              <p className="hint">{t("passport.valuesNote")}</p>
              {claims.map((c) => (
                <label key={c.id} className="flex cursor-pointer items-start gap-3 rounded-md border border-line bg-paper/50 p-3 has-[:checked]:border-pine has-[:checked]:bg-pine-soft">
                  <input type="checkbox" className="mt-1 h-5 w-5 shrink-0 accent-pine" data-testid={`claim-${c.id}`} checked={picked.includes(c.id)} onChange={(e) => setPicked((xs) => (e.target.checked ? [...xs, c.id] : xs.filter((x) => x !== c.id)))} />
                  <span className="font-semibold">{claimText(c)}</span>
                </label>
              ))}
            </fieldset>

            <div className="card border-pine bg-pine-soft" data-testid="share-preview" aria-live="polite">
              <h3 className="h2 flex items-center gap-2 text-pine-strong"><Icon name="shield-check" size={19} />{t("passport.previewTitle")}</h3>
              {picked.length === 0 ? <p className="mt-1 text-sm text-muted">{t("passport.previewEmpty")}</p> : (
                <ul className="mt-2 flex flex-col gap-1.5 text-sm">
                  {claims.filter((c) => picked.includes(c.id)).map((c) => (
                    <li key={c.id} className="flex items-start gap-2"><Icon name="check" size={17} className="mt-0.5 shrink-0 text-verified" />{claimText(c)}</li>
                  ))}
                </ul>
              )}
              <p className="mt-3 flex items-center gap-2 border-t border-dashed border-pine/40 pt-2 text-sm text-muted"><Icon name="lock" size={16} className="shrink-0" />{t("passport.hiddenCount", { n: hiddenCount })}</p>
            </div>

            <div className="card flex flex-col gap-3">
              <label className="field">{t("passport.audience")}
                <input data-testid="passport-audience" value={audience} onChange={(e) => setAudience(e.target.value)} maxLength={60} required className="input" />
              </label>
              <label className="field">{t("passport.challenge")}
                <input data-testid="passport-challenge" value={challenge} onChange={(e) => setChallenge(e.target.value.trim())} maxLength={64} className="input mono" />
              </label>
              <button type="submit" data-testid="passport-present" disabled={picked.length === 0 || !audience.trim()} className="btn btn-primary self-start"><Icon name="send" size={18} />{t("passport.present")}</button>
            </div>
          </form>
        </div>
      ) : null}

      {error ? <p role="alert" data-testid="passport-error" className="font-semibold text-deny">{error === "error" ? t("common.error") : error}</p> : null}
      {pending ? (
        <ProposalCard card={asCard(pending.proposal)} state={pending.state} shownAt={pending.shownAt} overrides={pending.claimIds.length ? { shares_only: (list?.passports[0]?.claims ?? []).filter((c) => pending.claimIds.includes(c.id)).map(claimText) } : undefined} onConfirm={(c) => void confirm(c)} onCancel={() => setPending({ ...pending, state: "cancelled" })} />
      ) : null}
      {receipt ? <p className="enter flex items-start gap-2 rounded-md border border-verified bg-verified-soft px-4 py-3 text-sm" data-testid="passport-receipt"><Icon name="check-circle" size={20} className="mt-0.5 shrink-0 text-verified" />{receipt}</p> : null}
      {presentation ? (
        <div className="card">
          <label htmlFor="presentation" className="h2">{t("passport.presentation")}</label>
          <textarea id="presentation" data-testid="presentation-text" readOnly rows={5} value={presentation} className="input mono mt-2 w-full break-all text-xs" />
        </div>
      ) : null}
    </section>
  );
}
