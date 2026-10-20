/** Production stand-in: nothing here reads demo tokens. Every persona lookup fails closed. */
export const PERSONA_COOKIE = "";
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function isPersona(_v: unknown): _v is never {
  return false;
}
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function tokenFor(_persona: never): string | null {
  return null;
}
