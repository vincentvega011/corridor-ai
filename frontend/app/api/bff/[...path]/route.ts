import { NextRequest, NextResponse } from "next/server";
const DEMO = process.env.NEXT_PUBLIC_DEMO_PERSONAS === "1";
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

/**
 * Same-origin gateway to the sandbox API. It adds the persona's bearer token (kept on the server), forwards
 * only the routes the customer app uses, and streams server-sent events through untouched. It never edits a
 * request or a response body: the proposal the user sees is the server's, and the hash goes back as sent.
 */
const ALLOWED: { method: string; pattern: RegExp; auth: boolean }[] = [
  { method: "POST", pattern: /^chat$/, auth: true },
  { method: "POST", pattern: /^chat\/[0-9a-f]{32}\/(confirm|cancel)$/, auth: true },
  { method: "POST", pattern: /^attachments$/, auth: true },
  { method: "GET", pattern: /^proposals\/pending$/, auth: true },
  { method: "GET", pattern: /^accounts$/, auth: true },
  { method: "GET", pattern: /^payees$/, auth: true },
  { method: "POST", pattern: /^payees\/[A-Za-z0-9_\-]{1,64}\/verification-request$/, auth: true },
  { method: "GET", pattern: /^transfers$/, auth: true },
  { method: "POST", pattern: /^routes\/compare$/, auth: true },
  { method: "GET", pattern: /^tuition\/plan$/, auth: true },
  { method: "GET", pattern: /^contributions$/, auth: true },
  { method: "POST", pattern: /^contributions\/[A-Za-z0-9_\-]{1,64}\/respond$/, auth: true },
  { method: "GET", pattern: /^passport$/, auth: true },
  { method: "POST", pattern: /^passport\/(issue|present|confirm)$/, auth: true },
  { method: "POST", pattern: /^passport\/(challenge|verify)$/, auth: false },
  { method: "GET", pattern: /^clock$/, auth: false },
  { method: "POST", pattern: /^unlock$/, auth: true },
  // sandbox authenticator stand-in: only a demo build forwards it
  ...(DEMO ? [{ method: "GET", pattern: /^demo\/authenticator$/, auth: true }] : []),
];

async function forward(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const joined = path.join("/");
  const rule = ALLOWED.find((r) => r.method === request.method && r.pattern.test(joined));
  if (!rule) return NextResponse.json({ error: "not available" }, { status: 404 });

  const headers = new Headers();
  const type = request.headers.get("content-type");
  if (type) headers.set("content-type", type);
  if (rule.auth) {
    // customer sign in is the sandbox persona cookie: present only in a demo build (lib/demo.ts)
    if (!DEMO) return NextResponse.json({ error: "sign in is not available in this build" }, { status: 401 });
    const { PERSONA_COOKIE, isPersona, tokenFor } = await import("@demo/session");
    const persona = request.cookies.get(PERSONA_COOKIE)?.value;
    const token = isPersona(persona) ? tokenFor(persona) : null;
    if (!token) return NextResponse.json({ error: "choose a persona first" }, { status: 401 });
    headers.set("authorization", `Bearer ${token}`);
  }
  const url = new URL(`${BACKEND_URL}/${joined}`);
  url.search = request.nextUrl.search;
  const upstream = await fetch(url, {
    method: request.method,
    headers,
    body: request.method === "GET" ? undefined : await request.arrayBuffer(),
    cache: "no-store",
    redirect: "manual",
  }).catch(() => null);
  if (!upstream) return NextResponse.json({ error: "the sandbox API is not reachable" }, { status: 502 });

  const out = new Headers();
  for (const name of ["content-type", "x-thread-id", "cache-control"]) {
    const v = upstream.headers.get(name);
    if (v) out.set(name, v);
  }
  out.set("cache-control", "no-store");
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export const GET = forward;
export const POST = forward;
export const dynamic = "force-dynamic";
