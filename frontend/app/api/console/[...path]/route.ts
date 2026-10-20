import { NextRequest, NextResponse } from "next/server";
import { BACKEND_URL, OFFICER_COOKIE } from "@/lib/server/officer";

/**
 * Same-origin gateway for the bank console: adds the logged in officer's own credential (from an httpOnly
 * cookie) and forwards only the officer routes the console uses. It never edits a body.
 */
const ALLOWED: { method: string; pattern: RegExp }[] = [
  { method: "GET", pattern: /^officer\/(me|ledger|ledger\/verify|risk\/queue|eval\/summary|verification-requests|cases)$/ },
  { method: "GET", pattern: /^officer\/proposals\/[0-9a-f]{64}\/trace$/ },
  { method: "GET", pattern: /^officer\/compare\/[A-Za-z0-9_\-]{1,64}$/ },
  { method: "POST", pattern: /^officer\/payees\/[A-Za-z0-9_\-]{1,64}\/verify$/ },
  { method: "GET", pattern: /^clock$/ },
];

async function forward(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const joined = path.join("/");
  if (!ALLOWED.some((r) => r.method === request.method && r.pattern.test(joined))) {
    return NextResponse.json({ error: "not available" }, { status: 404 });
  }
  const headers = new Headers();
  const type = request.headers.get("content-type");
  if (type) headers.set("content-type", type);
  if (joined !== "clock") {
    const credential = request.cookies.get(OFFICER_COOKIE)?.value;
    if (!credential) return NextResponse.json({ error: "log in first" }, { status: 401 });
    headers.set("authorization", `Bearer ${credential}`);
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
  return new Response(upstream.body, {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json", "cache-control": "no-store" },
  });
}

export const GET = forward;
export const POST = forward;
export const dynamic = "force-dynamic";
