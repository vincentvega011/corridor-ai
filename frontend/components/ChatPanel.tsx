"use client";

import { useRef, useState } from "react";
import { AgentFeed } from "@/components/AgentFeed";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { Icon } from "@/components/ui/Icon";
import { TrustChip, type TrustKind } from "@/components/ui/TrustChip";
import type { useAgent } from "@/lib/useAgent";

type Agent = ReturnType<typeof useAgent>;

const LEGEND: TrustKind[] = ["verified", "typed", "document", "held"];

/** What the four trust marks mean, shown once on the empty chat so the rest of the app reads at a glance. */
function Legend() {
  const { t } = useI18n();
  return (
    <section className="card flex flex-col gap-3" aria-label={t("legend.title")} data-testid="legend">
      <h2 className="eyebrow">{t("legend.title")}</h2>
      <ul className="flex flex-col gap-2.5">
        {LEGEND.map((k) => (
          <li key={k} className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <TrustChip kind={k} />
            <span className="text-sm text-muted">{t(`legend.${k}`)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The message box plus the feed. Shared by the chat page, the payees form and the tuition plan. */
export function ChatPanel({ agent, showInput = true }: { agent: Agent; showInput?: boolean }) {
  const { t } = useI18n();
  const { persona } = useSession();
  const [text, setText] = useState("");
  const [attached, setAttached] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const v = text;
    setText("");
    setAttached(null);
    await agent.send(v);
  }

  return (
    <div className="flex flex-col gap-3">
      {agent.items.length === 0 && !persona ? <p className="text-muted" data-testid="chat-no-persona">{t("chat.noPersona")}</p> : null}
      {agent.items.length === 0 && showInput ? <Legend /> : null}
      <AgentFeed items={agent.items} onConfirm={(id, c) => void agent.confirm(id, c)} onCancel={(id) => void agent.cancel(id)} onPick={(o) => void agent.send(o)} />
      {agent.busy ? (
        <p className="enter flex items-center gap-2 text-sm text-muted" role="status">
          <span className="flex gap-1" aria-hidden="true"><span className="skeleton h-2 w-2 rounded-md" /><span className="skeleton h-2 w-2 rounded-md" /><span className="skeleton h-2 w-2 rounded-md" /></span>
          {t("chat.working")}...
        </p>
      ) : null}
      {showInput ? (
        <form onSubmit={(e) => void submit(e)} className="dock sticky bottom-[4.6rem] z-10 -mx-4 flex flex-col gap-2 border-t border-line px-4 py-3 lg:bottom-0 lg:mx-0 lg:rounded-md lg:border lg:px-3">
          {attached ? <p className="flex items-center gap-1.5 text-xs text-doc" data-testid="attached-note"><TrustChip kind="document" />{t("chat.attached", { name: attached })}. {t("chat.attachNote")}</p> : null}
          <div className="flex gap-2">
            <label className="sr-only" htmlFor="chat-input">{t("chat.placeholder")}</label>
            <input
              id="chat-input"
              data-testid="chat-input"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={t("chat.placeholder")}
              maxLength={2000}
              className="input min-w-0 flex-1 rounded-md px-4"
              autoComplete="off"
            />
            <input
              ref={file}
              type="file"
              accept="application/pdf,image/png,image/jpeg"
              className="sr-only"
              data-testid="attach-input"
              aria-label={t("chat.attach")}
              onChange={async (e) => {
                const f = e.target.files?.[0];
                if (f && (await agent.attach(f))) setAttached(f.name);
              }}
            />
            <button type="button" onClick={() => file.current?.click()} className="btn btn-quiet h-11 w-11 shrink-0 !rounded-md !p-0" aria-label={t("chat.attach")} title={t("chat.attachNote")}>
              <Icon name="paperclip" size={20} />
            </button>
            <button type="submit" data-testid="send-button" disabled={agent.busy || !text.trim()} className="btn btn-primary shrink-0">
              <span className="hidden sm:inline">{t("chat.send")}</span><Icon name="send" size={18} /><span className="sr-only sm:hidden">{t("chat.send")}</span>
            </button>
          </div>
        </form>
      ) : null}
    </div>
  );
}
