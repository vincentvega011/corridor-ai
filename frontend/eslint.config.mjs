import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

// Rules that protect the invariants of the customer app (docs/spec/frontend.md, INV-3, INV-5):
// no raw HTML from the model or a document, no token in browser storage.
export default defineConfig([
  ...nextVitals,
  ...nextTs,
  globalIgnores([".next/**", ".next-e2e/**", ".next-prod/**", "out/**", "node_modules/**", "e2e/**", "playwright-report/**", "test-results/**"]),
  {
    rules: {
      "react/no-danger": "error",
      "no-restricted-globals": ["error", "localStorage", "sessionStorage"],
      "no-restricted-properties": [
        "error",
        { object: "window", property: "localStorage", message: "Tokens never go to browser storage" },
        { object: "window", property: "sessionStorage", message: "Tokens never go to browser storage" },
        { object: "document", property: "cookie", message: "Cookies are set by the server (httpOnly)" },
      ],
    },
  },
]);
