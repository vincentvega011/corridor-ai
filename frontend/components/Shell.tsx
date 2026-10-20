"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { I18nProvider, useI18n } from "@/lib/i18n";
import { SessionProvider, useSession } from "@/lib/session";
import { ConsoleFrame } from "@/components/console/ConsoleFrame";
import { LangSwitch } from "@/components/LangSwitch";
import { BrandMark } from "@/components/ui/Brand";
import { Icon, type IconName } from "@/components/ui/Icon";
import dynamic from "next/dynamic";

// Sandbox only: with the flag unset the condition is false at build time and the switcher is not in the bundle.
const PersonaSwitcher =
  process.env.NEXT_PUBLIC_DEMO_PERSONAS === "1"
    ? dynamic(() => import("@demo/persona-switcher").then((m) => m.PersonaSwitcher))
    : null;

const DemoAuthenticator =
  process.env.NEXT_PUBLIC_DEMO_PERSONAS === "1"
    ? dynamic(() => import("@demo/authenticator").then((m) => m.DemoAuthenticator))
    : null;

type NavItem = readonly [href: string, key: string, icon: IconName];
const PRIMARY: readonly NavItem[] = [
  ["/", "nav.chat", "chat"],
  ["/payees", "nav.payees", "payees"],
  ["/routes", "nav.routes", "route"],
  ["/tuition", "nav.tuition", "tuition"],
];
const SECONDARY: readonly NavItem[] = [
  ["/family", "nav.family", "family"],
  ["/passport", "nav.passport", "passport"],
  ["/verify", "nav.verify", "badge"],
];

/**
 * Page frame. The context strip (sandbox, demo date) is on every screen and cannot be dismissed; it is calm on
 * purpose: the sandbox is a fact about the product, not an error. Sidebar from 1024 px, bottom navigation below.
 */
function Frame({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const { clock } = useSession();
  const path = usePathname();
  const isConsole = path.startsWith("/console");
  const [more, setMore] = useState(false);

  useEffect(() => {
    if ("serviceWorker" in navigator) void navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  }, []);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- close the sheet when the page changes
  useEffect(() => { setMore(false); }, [path]);

  if (isConsole) return <ConsoleFrame>{children}</ConsoleFrame>;

  const current = (href: string) => (path === href ? ("page" as const) : undefined);
  const moreActive = SECONDARY.some(([h]) => h === path);

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[16.5rem_minmax(0,1fr)]">
      <aside className="hidden border-r border-line bg-surface lg:flex lg:flex-col lg:gap-6 lg:px-4 lg:py-6">
        <Link href="/" className="flex items-center gap-2.5 px-2 no-underline">
          <BrandMark />
          <span className="font-display text-xl font-semibold text-ink">{t("app.name")}</span>
        </Link>
        <nav aria-label={t("nav.main")} className="flex flex-col gap-1">
          {[...PRIMARY, ...SECONDARY].map(([href, key, icon]) => (
            <Link key={href} href={href} aria-current={current(href)} className="navlink">
              <Icon name={icon} size={20} />
              {t(key)}
            </Link>
          ))}
        </nav>
        <p className="mt-auto flex items-start gap-2 px-2 text-xs text-muted"><Icon name="shield" size={16} className="mt-0.5 shrink-0" />{t("shell.promise")}</p>
      </aside>

      <div className="flex min-h-dvh min-w-0 flex-col">
        <div role="status" data-testid="sandbox-banner" className="strip">
          <span className="inline-flex items-center gap-1.5"><Icon name="flask" size={15} /><b>{t("banner.sandbox")}</b></span>
        </div>
        <header className="flex items-center justify-between gap-2 border-b border-line bg-surface px-3 py-2 sm:px-4 lg:px-8">
          <Link href="/" className="flex shrink-0 items-center gap-2 no-underline lg:hidden" aria-label={t("app.name")}>
            <BrandMark size={30} />
            <span className="hidden font-display text-lg font-semibold text-ink min-[440px]:inline">{t("app.name")}</span>
          </Link>
          <div className="ml-auto flex min-w-0 items-center justify-end gap-2">
            {PersonaSwitcher && clock?.sandbox ? <PersonaSwitcher /> : null}
            <LangSwitch />
          </div>
        </header>
        {DemoAuthenticator && clock?.sandbox ? <DemoAuthenticator /> : null}
        <main className="mx-auto w-full max-w-3xl flex-1 px-4 pb-28 pt-4 lg:px-8 lg:pb-10 lg:pt-8">{children}</main>
        <footer className="px-4 pb-24 text-center text-xs text-muted lg:pb-6">{t("common.sandboxNote")}</footer>
      </div>

      {more ? (
        <div className="fixed inset-x-3 bottom-[4.6rem] z-30 rounded-md border border-line bg-surface p-2 enter lg:hidden" data-testid="more-sheet">
          {SECONDARY.map(([href, key, icon]) => (
            <Link key={href} href={href} aria-current={current(href)} className="navlink">
              <Icon name={icon} size={20} />
              {t(key)}
            </Link>
          ))}
        </div>
      ) : null}
      <nav aria-label={t("nav.main")} className="fixed inset-x-0 bottom-0 z-40 flex border-t border-line bg-surface pb-[env(safe-area-inset-bottom)] lg:hidden">
        {PRIMARY.map(([href, key, icon]) => (
          <Link key={href} href={href} aria-current={current(href)} className="tab">
            <span className="tab-icon"><Icon name={icon} size={21} /></span>
            {t(key)}
          </Link>
        ))}
        <button type="button" data-testid="nav-more" aria-expanded={more} aria-current={moreActive ? "page" : undefined} onClick={() => setMore((v) => !v)} className="tab">
          <span className="tab-icon"><Icon name="more" size={21} /></span>
          {t("nav.more")}
        </button>
      </nav>
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  return (
    <I18nProvider>
      <SessionProvider>
        <Frame>{children}</Frame>
      </SessionProvider>
    </I18nProvider>
  );
}
