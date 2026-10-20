import fs from "node:fs";
import path from "node:path";

/** The demo personas of the sandbox (never real people). */
export const PERSONAS = ["usr_priya", "usr_ramesh", "usr_anil"] as const;
export type Persona = (typeof PERSONAS)[number];
export const PERSONA_COOKIE = "corridor_persona";
export const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

export function isPersona(v: unknown): v is Persona {
  return typeof v === "string" && (PERSONAS as readonly string[]).includes(v);
}

let cache: Record<string, string> | null = null;

/**
 * The sandbox bearer token of a persona, read on the server only (from the secrets file the sandbox
 * writes). It is never sent to the browser: the browser holds an httpOnly cookie naming the persona and
 * this server adds the Authorization header. A real deployment would put a login here instead.
 */
export function tokenFor(persona: Persona): string | null {
  if (!cache) {
    const file =
      process.env.CORRIDOR_TOKENS_FILE ?? path.resolve(process.cwd(), "..", "secrets", "demo_tokens.json");
    try {
      cache = JSON.parse(fs.readFileSync(/*turbopackIgnore: true*/ file, "utf-8")) as Record<string, string>;
    } catch {
      return null;
    }
  }
  return cache[persona] ?? null;
}
