"use client";

import { useEffect, useState } from "react";
import type { ConfirmRequestData } from "@/lib/contracts";
import { phraseText } from "@/lib/display";
import { useI18n } from "@/lib/i18n";
import { CodeBoxes } from "@/components/ui/CodeBoxes";
import { CountdownRing } from "@/components/ui/CountdownRing";
import { Icon } from "@/components/ui/Icon";
import { OutcomeBadge } from "@/components/ui/OutcomeBadge";
import { TrustChip } from "@/components/ui/TrustChip";

const GRANT_SECONDS = 120; // a confirmation is a single use grant that lives 120 seconds (INV-5)

/** Show a value from the server as plain text. Objects are shown as JSON text, never as markup. */
function show(v: unknown, locale: "en" | "ne" | "zh" = "en"): string {
  if (v === null || v === undefined) return "-";
  if (typeof v === "string") return v;
  if (typeof v === "object" && v !== null && "en" in v) {
    // a note the server sends in every language: pick the user's
    const m = v as Record<string, unknown>;
    return String(m[locale === "zh" ? "en" : locale] ?? m.en);
  }
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

function Row({ label, value, items, testId }: { label: string; value: string; items?: string[]; testId?: string }) {
  return (
    <div className="fieldrow">
      <dt>{label}</dt>
      <dd className="num" data-testid={testId}>
        {items ? (
          <ul className="list-disc pl-5">{items.map((x) => <li key={x}>{x}</li>)}</ul>
        ) : value}
      </dd>
    </div>
  );
}

/** First 8 and last 4 hex characters: enough to compare by eye, the full value is one tap away. */
function shortFingerprint(h: string): string {
  return h.length > 14 ? `${h.slice(0, 8)}\u2026${h.slice(-4)}` : h;
}

/** Which "nothing happens if it changes" sentence fits the action: a payment, a share, an issue, a save, a request. */
function hashNote(action: unknown): string {
  switch (action) {
    case "share_passport": return "card.sentHash.share";
    case "issue_passport": return "card.sentHash.issue";
    case "add_payee": return "card.sentHash.save";
    case "request_contribution": return "card.sentHash.ask";
    default: return "card.sentHash.pay";
  }
}

interface Props {
  card: ConfirmRequestData;
  state: "open" | "sent" | "cancelled" | "done";
  shownAt: number;
  onConfirm: (totp: string | null) => void;
  onCancel: () => void;
  note?: string;
  /** Display values the page knows better than the server text (for example claim figures), by field name. */
  overrides?: Record<string, string[]>;
}

/**
 * The card the user signs (What You See Is What You Sign). Every field the grant binds is shown: the
 * action, amount and currency, payee label with the masked account, source account, route with fees and
 * arrival time. The client never computes or edits any of it; confirming sends the server's own
 * `proposal_hash` back (INV-5). A grant lives 120 s, so the card counts down.
 */
export function ProposalCard({ card, state, shownAt, onConfirm, onCancel, note, overrides }: Props) {
  const { t, num, locale, fmtDate } = useI18n();
  const [code, setCode] = useState("");
  const [left, setLeft] = useState(GRANT_SECONDS);
  const needsCode = card.required_level === "mfa";

  useEffect(() => {
    if (state !== "open") return;
    const tick = () => setLeft(Math.max(0, GRANT_SECONDS - Math.floor((Date.now() - shownAt) / 1000)));
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [shownAt, state]);

  const display = card.display ?? {};
  const route = (card.route ?? null) as Record<string, unknown> | null;
  const hashed = card.hashed_fields ?? {};
  const expired = state === "open" && left === 0;
  const codeOk = !needsCode || /^\d{6}$/.test(code);

  if (state === "done") {
    // once confirmed, the card collapses to a one line receipt (the full card stays in the ledger)
    const act = String(display.action ?? "");
    const label = t(`card.action.${act}`) === `card.action.${act}` ? act.replace(/_/g, " ") : t(`card.action.${act}`);
    const amount = typeof display.amount === "string" ? ` ${num(display.amount)}` : "";
    return (
      <p data-testid="proposal-card" data-state="done" className="enter flex items-center gap-2.5 rounded-md border border-verified bg-verified-soft px-4 py-3 text-sm font-semibold text-ink">
        <span className="pop grid h-7 w-7 shrink-0 place-items-center rounded-md bg-verified text-on-solid"><Icon name="check" size={16} className="draw" /></span>
        <span data-testid="card-final">{t("card.receipt", { action: `${label}${amount}` })}</span>
      </p>
    );
  }

  const step = card.plan_step as { n?: number; total?: number } | null | undefined;
  return (
    <article
      aria-label={t("card.title")}
      data-testid="proposal-card"
      data-state={state}
      className={`instrument enter ${state === "cancelled" ? "opacity-70 saturate-50" : ""}`}
    >
      <div className="instrument-band">
        <div>
          <p className="eyebrow">{step?.n && step.total ? t("card.step", { n: step.n, total: step.total }) : t("card.signedLabel")}</p>
          <h3 className="font-display text-lg font-semibold leading-tight">{t("card.title")}</h3>
        </div>
        <span data-testid="card-level">
          <OutcomeBadge level={needsCode ? "mfa" : "confirm"} label={needsCode ? t("card.level.mfa") : t("card.level.confirm")} />
        </span>
      </div>

      <div className="px-4 pb-1 pt-3">
        {card.warnings && card.warnings.length > 0 ? (
          <div role="alert" className="mb-3 rounded-md border border-dashed border-doc bg-doc-soft px-3 py-2.5" data-testid="card-warnings">
            <TrustChip kind="document" />
            <ul className="mt-1.5 list-disc pl-5 text-sm text-ink">
              {card.warnings.map((w) => (
                <li key={w}>{t(`warn.${w}`) === `warn.${w}` ? w : t(`warn.${w}`)}</li>
              ))}
            </ul>
          </div>
        ) : null}

        <dl>
          {Object.entries(display).filter(([k, v]) => !(k === "amount_source" && v === "funding plan")).map(([k, v]) => {
            const into = k === "from_account" && typeof v === "string" && v.startsWith("into ");
            const label = into ? t("card.field.paid_into") : t(`card.field.${k}`) === `card.field.${k}` ? k.replace(/_/g, " ") : t(`card.field.${k}`);
            const list = overrides?.[k] ?? (Array.isArray(v) ? v.map((x) => phraseText(show(x, locale), t, fmtDate)) : undefined);
            const text =
              k === "action" && typeof v === "string" && t(`card.action.${v}`) !== `card.action.${v}`
                ? t(`card.action.${v}`)
                : phraseText(show(v, locale), t, fmtDate);
            return <Row key={k} label={label} value={num(text)} items={list?.map((x) => num(x))} testId={`field-${k}`} />;
          })}
        </dl>

        {route ? (
          <section className="my-3 rounded-md bg-pine-soft p-3" aria-label={t("card.route")} data-testid="card-route">
            <h4 className="eyebrow mb-1 flex items-center gap-1.5 text-pine-strong"><Icon name="route" size={15} />{t("card.route")}</h4>
            <dl>
              {route.provider ? <Row label={t("card.route.provider")} value={show(route.provider)} /> : null}
              {route.send ? <Row label={t("card.route.send")} value={num(show(route.send))} /> : null}
              {route.cost ? <Row label={t("card.route.cost")} value={num(show(route.cost))} /> : null}
              {route.delivered ? <Row label={t("card.route.delivered")} value={num(show(route.delivered))} /> : null}
              {route.effective_rate ? <Row label={t("card.route.rate")} value={num(show(route.effective_rate))} /> : null}
              {route.eta_hours !== undefined && route.eta_hours !== null ? <Row label={t("card.route.eta", { h: show(route.eta_hours) })} value="" /> : null}
              {route.saving_vs_default ? <Row label={t("card.route.saving")} value={num(show(route.saving_vs_default))} /> : null}
            </dl>
            {route.rate_date ? <p className="mt-1 text-xs text-muted" data-testid="card-rate-source">{t("rate.line", { date: fmtDate(String(route.rate_date)) })}</p> : null}
          </section>
        ) : null}

        {card.policy_reasons && card.policy_reasons.length > 0 ? (
          <details className="mt-2 text-sm">
            <summary className="cursor-pointer font-semibold text-pine">{t("card.reasons")}</summary>
            <ul className="mt-1 list-disc pl-5 text-muted">
              {card.policy_reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </details>
        ) : null}

        <details className="mt-3 text-xs text-muted">
          <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-semibold text-ink">
            <Icon name="lock" size={16} className="text-pine" />
            <span>{t("card.hash")}</span>
            <span className="fp text-muted" data-testid="card-fingerprint">{shortFingerprint(card.proposal_hash)}</span>
          </summary>
          <p className="fp mt-2 break-all" data-testid="card-hash">{card.proposal_hash}</p>
          <pre className="fp mt-1 overflow-x-auto whitespace-pre-wrap break-all">{JSON.stringify(hashed, null, 1)}</pre>
        </details>
        <p className="mb-3 mt-1 text-xs text-muted">{t(hashNote(display.action))}</p>
      </div>

      {state === "open" || state === "sent" ? (
        <form
          className="flex flex-col gap-3 border-t border-line bg-paper/60 px-4 py-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (!expired && codeOk) onConfirm(needsCode ? code : null);
          }}
        >
          {needsCode ? <CodeBoxes value={code} onChange={setCode} label={t("card.code")} /> : null}
          <div className="flex items-center gap-3">
            <CountdownRing left={left} total={GRANT_SECONDS} label={expired ? t("card.expired") : t("card.expires", { s: left })} />
            <p className={`text-sm ${expired ? "font-semibold text-deny" : "text-muted"}`} data-testid="card-countdown" aria-live="polite">
              {expired ? t("card.expired") : t("card.expires", { s: left })}
            </p>
          </div>
          {note ? <p role="alert" className="text-sm font-semibold text-deny">{t("common.error")}</p> : null}
          <div className="flex flex-wrap gap-2">
            <button type="submit" data-testid="confirm-button" disabled={state === "sent" || expired || !codeOk} className="btn btn-primary">
              <Icon name="check" size={18} />
              {t("card.confirm")}
            </button>
            <button type="button" data-testid="cancel-button" onClick={onCancel} disabled={state === "sent"} className="btn btn-quiet">
              {t("card.cancel")}
            </button>
          </div>
        </form>
      ) : (
        <p className="border-t border-line px-4 py-3 text-sm font-semibold" data-testid="card-final">{t("common.cancel")}</p>
      )}
      <p className="instrument-seal"><Icon name="shield-check" size={18} className="shrink-0" />{t("card.seal")}</p>
    </article>
  );
}
