/**
 * Sandbox-only features (the persona switcher and the route that signs a visitor in as a demo persona) exist
 * only in a build made with NEXT_PUBLIC_DEMO_PERSONAS=1. The flag is inlined at build time, so a production
 * build contains none of that code (checked by scripts/check-production-build.mjs).
 */
export const DEMO_PERSONAS = process.env.NEXT_PUBLIC_DEMO_PERSONAS === "1";
