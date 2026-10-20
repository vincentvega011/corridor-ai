"use client";

import { ChatPanel } from "@/components/ChatPanel";
import { PageTitle } from "@/components/ui/States";
import { useI18n } from "@/lib/i18n";
import { useAgent } from "@/lib/useAgent";

export default function ChatPage() {
  const { t } = useI18n();
  const agent = useAgent();
  return (
    <section aria-labelledby="chat-title" className="flex flex-col gap-4">
      <PageTitle id="chat-title" eyebrow={t("chat.eyebrow")} sub={t("chat.sub")}>{t("chat.title")}</PageTitle>
      <ChatPanel agent={agent} />
    </section>
  );
}
