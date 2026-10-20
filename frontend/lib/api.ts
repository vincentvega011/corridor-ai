import type { AgentEvent } from "@/lib/contracts";

/** Calls go to this app's own /api/bff, which adds the persona's token on the server. */
const BASE = "/api/bff";

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(`api ${status}`);
  }
}

export async function api<T>(path: string, init?: { method?: "GET" | "POST"; body?: unknown }): Promise<T> {
  const res = await fetch(`${BASE}/${path}`, {
    method: init?.method ?? "GET",
    headers: init?.body !== undefined ? { "content-type": "application/json" } : undefined,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    cache: "no-store",
  });
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) throw new ApiError(res.status, (data as { detail?: unknown } | null)?.detail ?? data);
  return data as T;
}

/** Read a server-sent event stream (POST) and call `onEvent` for each agent event, in order. */
export async function streamEvents(
  path: string,
  body: unknown,
  onEvent: (e: AgentEvent) => void,
): Promise<{ threadId: string | null }> {
  const res = await fetch(`${BASE}/${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok || !res.body) {
    const detail = await res.json().catch(() => null);
    throw new ApiError(res.status, detail?.detail ?? detail);
  }
  const threadId = res.headers.get("x-thread-id");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut = buffer.indexOf("\n\n");
    while (cut >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const line = block.split("\n").find((l) => l.startsWith("data:"));
      if (line) {
        try {
          onEvent(JSON.parse(line.slice(5).trim()) as AgentEvent);
        } catch {
          /* a malformed event is ignored, never rendered */
        }
      }
      cut = buffer.indexOf("\n\n");
    }
  }
  return { threadId };
}
