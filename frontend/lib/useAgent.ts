"use client";

import { useCallback, useRef, useState } from "react";
import { ApiError, api, streamEvents } from "@/lib/api";
import type { AgentEvent, ConfirmRequestData, MessageData, RejectedData, StructuredAction } from "@/lib/contracts";

export type FeedItem =
  | { kind: "user"; id: string; text: string }
  | { kind: "assistant"; id: string; text: string; tone: "info" | "done" | "refused" | "error"; short?: "held" | "done"; acct?: string }
  | { kind: "clarify"; id: string; question: string; options: string[] }
  | { kind: "card"; id: string; thread: string; card: ConfirmRequestData; shownAt: number; state: "open" | "sent" | "cancelled" | "done"; note?: string }
  | { kind: "hold"; id: string; rejected: RejectedData }
  | { kind: "receipt"; id: string; lines: string[] };

let counter = 0;
const nid = () => `i${++counter}`;

/**
 * One conversation with the agent. Everything shown comes from the server's events as plain data: the card
 * is the server's canonical proposal and confirming sends back its `proposal_hash` untouched (INV-5).
 */
export function useAgent() {
  const [items, setItems] = useState<FeedItem[]>([]);
  const [busy, setBusy] = useState(false);
  const thread = useRef<string | null>(null);
  const docs = useRef<string[]>([]);
  const errored = useRef(false);

  const push = useCallback((item: FeedItem) => setItems((xs) => [...xs, item]), []);

  const handle = useCallback((e: AgentEvent, threadId: string | null) => {
    const data = e.data as Record<string, unknown>;
    switch (e.type) {
      case "message": {
        const m = data as unknown as MessageData;
        errored.current = false;
        // when a card or receipt carries the details, the bubble is one short line (the full text stays in the ledger)
        const full = String(m.text ?? "");
        const short = m.hold ? "held" : m.status === "executed" ? "done" : undefined;
        push({ kind: "assistant", id: nid(), text: full, tone: m.status === "executed" ? "done" : "info", short, acct: /\*{4}\d{4}/.exec(full)?.[0] });
        if (m.hold) {
          // a payee was saved: show the hold, its earliest time and the way to ask the bank (ADR 0018)
          push({ kind: "hold", id: nid(), rejected: { reason: "payee_saved", hold: m.hold, actions: m.actions ?? [] } as RejectedData });
        }
        break;
      }
      case "clarify":
        push({ kind: "clarify", id: nid(), question: String(data.question ?? ""), options: ((data.options as string[] | undefined) ?? []).map(String) });
        break;
      case "confirm_request":
        push({ kind: "card", id: nid(), thread: threadId ?? thread.current ?? "", card: data as unknown as ConfirmRequestData, shownAt: Date.now(), state: "open" });
        break;
      case "rejected": {
        const r = data as unknown as RejectedData;
        if ((r.actions && r.actions.length > 0) || r.hold) push({ kind: "hold", id: nid(), rejected: r });
        break; // other refusals are explained by the message event that follows
      }
      case "executed": {
        const d = data as Record<string, unknown>;
        const lines = ["amount", "payee", "balance_after"].filter((k) => d[k]).map((k) => `${k}: ${String(d[k])}`);
        if (lines.length) push({ kind: "receipt", id: nid(), lines });
        break;
      }
      case "error":
        errored.current = true; // the message event that follows explains it; shown only if none does
        break;
      default:
        break;
    }
  }, [push]);

  const run = useCallback(async (label: string, body: Record<string, unknown>) => {
    setBusy(true);
    errored.current = false;
    push({ kind: "user", id: nid(), text: label });
    try {
      const { threadId } = await streamEvents(
        "chat",
        { thread_id: thread.current ?? undefined, attachments: docs.current.map((doc_id) => ({ doc_id })), ...body },
        (e) => handle(e, thread.current),
      );
      docs.current = [];
      if (errored.current) push({ kind: "assistant", id: nid(), text: "", tone: "error" }); // an error with no explanation: one generic line
      if (threadId) thread.current = threadId;
      // cards that arrived before the thread id was known get it now
      setItems((xs) => xs.map((x) => (x.kind === "card" && !x.thread && threadId ? { ...x, thread: threadId } : x)));
    } catch (err) {
      push({ kind: "assistant", id: nid(), text: err instanceof ApiError && err.status === 401 ? "persona" : "", tone: "error" });
    } finally {
      setBusy(false);
    }
  }, [handle, push]);

  /** A typed message: the server understands it with the parser and the planner. */
  const send = useCallback(async (text: string) => {
    const message = text.trim();
    if (!message) return;
    await run(message, { message });
  }, [run]);

  /** A button: the server already knows what is meant, so no model is asked (ADR 0021). `label` is shown as the user's line. */
  const sendAction = useCallback(async (label: string, action: StructuredAction) => {
    await run(label, { message: "", action });
  }, [run]);

  const attach = useCallback(async (file: File): Promise<string | null> => {
    try {
      const res = await fetch("/api/bff/attachments", { method: "POST", body: file, headers: { "content-type": file.type || "application/octet-stream" } });
      if (!res.ok) return null;
      const { doc_id } = (await res.json()) as { doc_id: string };
      docs.current.push(doc_id);
      return doc_id;
    } catch {
      return null;
    }
  }, []);

  const confirm = useCallback(async (cardId: string, totp: string | null) => {
    const item = items.find((x) => x.id === cardId);
    if (!item || item.kind !== "card") return;
    setItems((xs) => xs.map((x) => (x.id === cardId && x.kind === "card" ? { ...x, state: "sent" } : x)));
    setBusy(true);
    try {
      let refused = false;
      await streamEvents(`chat/${item.thread}/confirm`, { proposal_hash: item.card.proposal_hash, totp: totp || undefined }, (e) => {
        if (e.type === "rejected") refused = true;
        handle(e, item.thread);
      });
      setItems((xs) => xs.map((x) => (x.id === cardId && x.kind === "card" ? { ...x, state: refused ? "open" : "done" } : x)));
    } catch {
      setItems((xs) => xs.map((x) => (x.id === cardId && x.kind === "card" ? { ...x, state: "open", note: "error" } : x)));
    } finally {
      setBusy(false);
    }
  }, [handle, items]);

  const cancel = useCallback(async (cardId: string) => {
    const item = items.find((x) => x.id === cardId);
    if (!item || item.kind !== "card") return;
    setItems((xs) => xs.map((x) => (x.id === cardId && x.kind === "card" ? { ...x, state: "cancelled" } : x)));
    try {
      await api(`chat/${item.thread}/cancel`, { method: "POST", body: {} });
    } catch {
      /* the card is already closed on screen; the server discards it on the next message anyway */
    }
  }, [items]);

  const reset = useCallback(() => { setItems([]); thread.current = null; docs.current = []; }, []);

  return { items, busy, send, sendAction, attach, confirm, cancel, reset };
}
