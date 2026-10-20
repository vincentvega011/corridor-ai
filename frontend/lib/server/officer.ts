/** The bank console's server side: one credential per officer, kept in an httpOnly cookie, never in script. */
export const OFFICER_COOKIE = "corridor_officer";
export const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

/** Ask the sandbox API whose credential this is. Returns the officer id, or null. */
export async function whoIs(credential: string): Promise<string | null> {
  if (!credential || credential.length > 200) return null;
  try {
    const res = await fetch(`${BACKEND_URL}/officer/me`, {
      headers: { authorization: `Bearer ${credential}` },
      cache: "no-store",
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { officer_id?: string };
    return body.officer_id ?? null;
  } catch {
    return null;
  }
}
