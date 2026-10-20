import { NextRequest, NextResponse } from "next/server";

// Sandbox only (lib/demo.ts): with NEXT_PUBLIC_DEMO_PERSONAS unset, this route answers 404 and the code that
// reads the demo tokens is not in the build.
const DEMO = process.env.NEXT_PUBLIC_DEMO_PERSONAS === "1";
const NOT_AVAILABLE = () => NextResponse.json({ error: "not available" }, { status: 404 });

/** GET: which persona this browser is using. POST {persona}: switch (sandbox feature). DELETE: sign out. */
export async function GET(request: NextRequest) {
  if (!DEMO) return NOT_AVAILABLE();
  const { PERSONA_COOKIE, isPersona, tokenFor } = await import("@demo/session");
  const persona = request.cookies.get(PERSONA_COOKIE)?.value;
  return NextResponse.json(
    { persona: isPersona(persona) && tokenFor(persona) ? persona : null },
    { headers: { "Cache-Control": "no-store" } },
  );
}

export async function POST(request: NextRequest) {
  if (!DEMO) return NOT_AVAILABLE();
  const { PERSONA_COOKIE, isPersona, tokenFor } = await import("@demo/session");
  const body = (await request.json().catch(() => null)) as { persona?: unknown } | null;
  const persona = body?.persona;
  if (!isPersona(persona) || !tokenFor(persona)) {
    return NextResponse.json({ error: "unknown persona" }, { status: 400 });
  }
  const res = NextResponse.json({ persona });
  res.cookies.set(PERSONA_COOKIE, persona, {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.COOKIE_SECURE === "1",
    path: "/",
    maxAge: 60 * 60 * 8,
  });
  return res;
}

export async function DELETE() {
  if (!DEMO) return NOT_AVAILABLE();
  const { PERSONA_COOKIE } = await import("@demo/session");
  const res = NextResponse.json({ persona: null });
  res.cookies.delete(PERSONA_COOKIE);
  return res;
}
