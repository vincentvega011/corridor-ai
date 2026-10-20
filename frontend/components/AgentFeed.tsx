"use client";

import { ProposalCard } from "@/components/ProposalCard";
import { HoldCard } from "@/components/HoldCard";
import { useI18n } from "@/lib/i18n";
import { BrandMark } from "@/components/ui/Brand";
import { Icon } from "@/components/ui/Icon";
import type { FeedItem } from "@/lib/useAgent";
import { serverZh } from "@/lib/serverText";

interface Props {
  items: FeedItem[];
  onConfirm: (id: string, totp: string | null) => void;
  onCancel: (id: string) => void;
  onPick: (text: string) => void;
}

/** The conversation. Every string from the server or a document is rendered as plain text (React escapes it). */
export function AgentFeed({ items, onConfirm, onCancel, onPick }: Props) {
  const { t, num, locale } = useI18n();
  const st = (x: string) => (locale === "zh" ? serverZh(x) : x); // fixed server replies in Chinese (display only)
  return (
    <ol className="flex flex-col gap-3.5" aria-live="polite" data-testid="feed">
      {items.map((it) => {
        switch (it.kind) {
          case "user":
            return (
              <li key={it.id} className="bubble-user enter self-end max-w-[85%] px-4 py-2.5" data-testid="msg-user">
                <span className="sr-only">{t("chat.you")}: </span>
                {it.text}
              </li>
            );
          case "assistant":
            return (
              <li key={it.id} className="enter flex max-w-[94%] items-end gap-2">
                <span className="mb-1 shrink-0"><BrandMark size={26} /></span>
                <div
                  data-testid="msg-assistant"
                  className={`bubble-agent px-4 py-2.5 ${it.tone === "error" ? "!border-deny !bg-deny-soft text-deny" : it.tone === "done" ? "!border-verified !bg-verified-soft" : ""}`}
                >
                  <span className="sr-only">{t("chat.assistant")}: </span>
                  {it.tone === "error" ? t(it.text === "persona" ? "chat.noPersona" : "common.error") : it.short === "held" ? (it.acct ? t("chat.heldShort", { acct: it.acct }) : t("chat.heldShortNoAcct")) : it.short === "done" ? t("chat.doneShort") : num(st(it.text))}
                </div>
              </li>
            );
          case "clarify":
            return (
              <li key={it.id} className="enter max-w-[94%] rounded-md border border-typed bg-typed-soft px-4 py-3" data-testid="msg-clarify">
                <p className="eyebrow flex items-center gap-1.5 text-typed"><Icon name="info" size={15} />{t("clarify.title")}</p>
                <p className="mt-0.5">{st(it.question)}</p>
                {it.options.length > 0 ? (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {it.options.map((o) => (
                      <button key={o} type="button" onClick={() => onPick(o)} className="btn btn-secondary btn-sm">
                        {o}
                      </button>
                    ))}
                  </div>
                ) : null}
              </li>
            );
          case "card":
            return (
              <li key={it.id}>
                <ProposalCard card={it.card} state={it.state} shownAt={it.shownAt} note={it.note} onConfirm={(c) => onConfirm(it.id, c)} onCancel={() => onCancel(it.id)} />
              </li>
            );
          case "hold":
            return (
              <li key={it.id}>
                <HoldCard rejected={it.rejected} />
              </li>
            );
          case "receipt":
            return (
              <li key={it.id} className="enter flex items-start gap-2.5 rounded-md border border-verified bg-verified-soft px-4 py-3 text-sm" data-testid="receipt">
                <Icon name="check-circle" size={20} className="mt-0.5 shrink-0 text-verified" />
                <div>
                  {it.lines.map((l) => {
                    const [k, ...rest] = l.split(": ");
                    const label = t(`receipt.${k}`);
                    return <div key={l}>{label === `receipt.${k}` ? num(l) : `${label}: ${num(rest.join(": "))}`}</div>;
                  })}
                </div>
              </li>
            );
        }
      })}
    </ol>
  );
}
