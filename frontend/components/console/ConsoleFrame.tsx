"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { Icon } from "@/components/ui/Icon";
import { LangSwitch } from "@/components/LangSwitch";
import { useI18n } from "@/lib/i18n";

const THEME_KEY = "corridor_console_theme"; // a display preference only, never a credential

const Officer = createContext<string | null>(null);
export const useOfficer = () => useContext(Officer);

const NAV = [
  ["/console/queue", "c.nav.queue"],
  ["/console/trace", "c.nav.trace"],
  ["/console/ledger", "c.nav.ledger"],
  ["/console/risk", "c.nav.risk"],
  ["/console/compare", "c.nav.compare"],
  ["/console/eval", "c.nav.eval"],
] as const;

/** The bank operations console frame: desktop layout, SANDBOX banner, per officer login. */
export function ConsoleFrame({ children }: { children: ReactNode }) {
  const { tc } = useI18n();
  const path = usePathname();
  const router = useRouter();
  const [officer, setOfficer] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const onLogin = path === "/console/login";

  useEffect(() => {
    try {
      // eslint-disable-next-line no-restricted-properties -- a display preference only, never a token
      if (window.localStorage.getItem(THEME_KEY) === "light") setTheme("light"); // eslint-disable-line react-hooks/set-state-in-effect
    } catch {
      /* the default theme applies */
    }
  }, []);
  function flip() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    try {
      // eslint-disable-next-line no-restricted-properties -- a display preference only, never a token
      window.localStorage.setItem(THEME_KEY, next);
    } catch {
      /* private mode: the choice lasts for this visit */
    }
  }

  useEffect(() => {
    void (async () => {
      try {
        const s = await (await fetch("/api/console/session", { cache: "no-store" })).json();
        setOfficer((s as { officer_id: string | null }).officer_id);
      } catch {
        setOfficer(null);
      }
      setChecked(true);
    })();
  }, [path]);

  useEffect(() => {
    if (checked && !officer && !onLogin) router.replace("/console/login");
    if (checked && officer && onLogin) router.replace("/console/queue"); // logged in: never leave the login card up
  }, [checked, officer, onLogin, router]);

  async function logout() {
    await fetch("/api/console/session", { method: "DELETE" });
    setOfficer(null);
    router.replace("/console/login");
  }

  return (
    <Officer.Provider value={officer}>
      <div className="console-root min-h-dvh" data-theme={theme}>
        <div role="status" data-testid="sandbox-banner" className="strip">
          <span className="inline-flex items-center gap-1.5"><Icon name="flask" size={14} /><b>{tc("c.sandbox")}</b></span>
        </div>
        <header className="sticky top-0 z-20 flex items-center justify-between gap-3 border-b border-line bg-surface/95 px-4 py-2 backdrop-blur">
          <div className="flex items-center gap-3">
            <span className="grid h-7 w-7 place-items-center rounded-md bg-pine text-on-solid"><Icon name="bank" size={17} /></span>
            <span className="text-sm font-semibold">{tc("c.title")}</span>
            <span className="hidden text-xs text-muted sm:inline">{tc("c.subtitle")}</span>
          </div>
          <div className="flex items-center gap-2 text-xs">
            {officer ? <span className="mono flex items-center gap-1.5 rounded-md border border-line bg-sand px-2 py-1" data-testid="officer-id"><Icon name="user" size={13} />{officer}</span> : null}
            <LangSwitch only={["en", "zh"]} />
            <button type="button" onClick={flip} className="btn btn-quiet btn-sm" aria-label={theme === "dark" ? tc("c.theme.toLight") : tc("c.theme.toDark")} title={theme === "dark" ? tc("c.theme.light") : tc("c.theme.dark")}>
              <Icon name={theme === "dark" ? "sun" : "moon"} size={15} />
            </button>
            {officer ? <button type="button" data-testid="logout" onClick={() => void logout()} className="btn btn-quiet btn-sm">{tc("c.logout")}</button> : null}
          </div>
        </header>
        {officer ? (
          <nav aria-label={tc("c.nav.label")} className="flex gap-0.5 overflow-x-auto border-b border-line bg-surface px-3">
            {NAV.map(([href, key]) => (
              <Link key={href} href={href} aria-current={path === href ? "page" : undefined} className={`whitespace-nowrap border-b-2 px-3 py-2.5 text-[0.8125rem] font-medium no-underline ${path === href ? "border-pine text-pine" : "border-transparent text-muted hover:text-ink"}`}>
                {tc(key)}
              </Link>
            ))}
          </nav>
        ) : null}
        <main className="mx-auto max-w-[88rem] px-4 py-5">{(onLogin && !officer) || (officer && !onLogin) ? children : null}</main>
      </div>
    </Officer.Provider>
  );
}
