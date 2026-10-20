import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { headers } from "next/headers";
import "./globals.css";
import { Shell } from "@/components/Shell";

// Fonts are files in this repository (SIL Open Font License, licence texts beside them in app/fonts): nothing is
// fetched from a font host. IBM Plex Sans is the face for all text and headings, IBM Plex Mono for amounts in tables, hashes and codes; Noto Sans Devanagari and a Noto Sans SC subset cover Nepali and Chinese.
const plexSans = localFont({
  variable: "--font-plex-sans",
  display: "swap",
  src: [
    { path: "./fonts/ibm-plex-sans-latin-400-normal.woff2", weight: "400", style: "normal" },
    { path: "./fonts/ibm-plex-sans-latin-500-normal.woff2", weight: "500", style: "normal" },
    { path: "./fonts/ibm-plex-sans-latin-600-normal.woff2", weight: "600", style: "normal" },
  ],
});
const plexMono = localFont({
  variable: "--font-plex-mono",
  display: "swap",
  src: [
    { path: "./fonts/ibm-plex-mono-latin-400-normal.woff2", weight: "400", style: "normal" },
    { path: "./fonts/ibm-plex-mono-latin-500-normal.woff2", weight: "500", style: "normal" },
  ],
});
const notoDeva = localFont({
  variable: "--font-noto-deva",
  display: "swap",
  src: [
    { path: "./fonts/noto-sans-devanagari-devanagari-400-normal.woff2", weight: "400", style: "normal" },
    { path: "./fonts/noto-sans-devanagari-devanagari-600-normal.woff2", weight: "600", style: "normal" },
    { path: "./fonts/noto-sans-devanagari-devanagari-700-normal.woff2", weight: "700", style: "normal" },
  ],
});
const notoSc = localFont({
  variable: "--font-noto-sc",
  display: "swap",
  src: [{ path: "./fonts/noto-sans-sc-subset-wght.woff2", weight: "400 700", style: "normal" }], // Simplified Chinese, subset (scripts/subset_zh_font.py)
});

export const metadata: Metadata = {
  title: "Corridor AI (sandbox)",
  description: "Cross-border and student finance assistant. SANDBOX: no real money.",
  manifest: "/manifest.webmanifest",
  icons: { icon: "/icons/icon-192.png", apple: "/icons/icon-192.png" },
};
export const viewport: Viewport = { themeColor: "#09090b", width: "device-width", initialScale: 1 };

// Every page is rendered per request so the Content-Security-Policy nonce from proxy.ts is fresh.
export const dynamic = "force-dynamic";

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  await headers(); // reading the request opts into dynamic rendering (nonce)
  return (
    <html lang="en" className={`${plexSans.variable} ${plexMono.variable} ${notoDeva.variable} ${notoSc.variable}`}>
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
