import path from "node:path";
import type { NextConfig } from "next";

// The customer app talks to the sandbox API only through its own /api/bff route (same origin), so the
// browser never holds a token and no request leaves for another host (strict CSP in proxy.ts).
// The persona switcher and the demo token reader are compiled in only for a demo build. Otherwise the two
// aliases below point at inert stand-ins, so the production bundle contains none of that code.
const demo = process.env.NEXT_PUBLIC_DEMO_PERSONAS === "1";
const aliases = {
  "@demo/persona-switcher": demo ? "./components/PersonaSwitcher.tsx" : "./lib/stubs/persona-switcher.tsx",
  "@demo/authenticator": demo ? "./components/DemoAuthenticator.tsx" : "./lib/stubs/no-authenticator.tsx",
  "@demo/session": demo ? "./lib/server/session.ts" : "./lib/stubs/demo-session.ts",
};

const nextConfig: NextConfig = {
  turbopack: { resolveAlias: aliases },
  webpack: (config) => {
    // the same aliases for `next build --webpack`, so the production exclusion does not depend on the bundler
    for (const [from, to] of Object.entries(aliases)) config.resolve.alias[from] = path.resolve(__dirname, to);
    return config;
  },
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  poweredByHeader: false,
  reactStrictMode: true,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
