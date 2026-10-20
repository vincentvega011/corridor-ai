import { NextRequest, NextResponse } from "next/server";
import { OFFICER_COOKIE, whoIs } from "@/lib/server/officer";

const NO_STORE = { "Cache-Control": "no-store" };

/** GET: who is logged in. POST {officer, credential}: log in as that officer. DELETE: log out. */
export async function GET(request: NextRequest) {
  const id = await whoIs(request.cookies.get(OFFICER_COOKIE)?.value ?? "");
  return NextResponse.json({ officer_id: id }, { headers: NO_STORE });
}

export async function POST(request: NextRequest) {
  const body = (await request.json().catch(() => null)) as { officer?: unknown; credential?: unknown } | null;
  const officer = typeof body?.officer === "string" ? body.officer.trim() : "";
  const credential = typeof body?.credential === "string" ? body.credential : "";
  const id = await whoIs(credential);
  // the credential must belong to the officer who typed their own id: one account per person
  if (!id || id !== officer) return NextResponse.json({ error: "wrong officer id or credential" }, { status: 401 });
  const res = NextResponse.json({ officer_id: id }, { headers: NO_STORE });
  res.cookies.set(OFFICER_COOKIE, credential, {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.COOKIE_SECURE === "1",
    path: "/",
    maxAge: 60 * 60 * 8,
  });
  return res;
}

export async function DELETE() {
  const res = NextResponse.json({ officer_id: null }, { headers: NO_STORE });
  res.cookies.delete(OFFICER_COOKIE);
  return res;
}

export const dynamic = "force-dynamic";
