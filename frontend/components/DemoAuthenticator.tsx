"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

// Own strings (not in the shipped locale files), so a production build carries none of this text.
const TEXT = {
  en: { title: "Demo authenticator (sandbox)", note: "A stand-in for the authenticator app on the user's phone. It exists only in this demo build.", code: "Current code", seconds: "Changes in {s} s", hide: "Hide", show: "Show the sandbox authenticator" },
  zh: { title: "演示验证器（沙盒）", note: "代替用户手机上的验证器应用，仅存在于此演示版本。", code: "当前验证码", seconds: "{s} 秒后更换", hide: "收起", show: "显示沙盒验证器" },
  ne: { title: "डेमो अथेन्टिकेटर (स्यान्डबक्स)", note: "प्रयोगकर्ताको फोनमा रहेको अथेन्टिकेटर एपको प्रतिनिधि। यो डेमो संस्करणमा मात्र हुन्छ।", code: "हालको कोड", seconds: "{s} सेकेन्डमा बदलिन्छ", hide: "लुकाउनुहोस्", show: "स्यान्डबक्स अथेन्टिकेटर देखाउनुहोस्" },
} as const;

interface Code { code: string; valid_for_seconds: number }

/**
 * Demo only: a stand-in for the authenticator app on the user's phone, so a recording can show where the
 * six digit code comes from. It shows the CURRENT persona's code and a countdown. The code is fetched from the
 * sandbox API, kept in memory only, never logged or stored, and this component is not in a production build.
 */
export function DemoAuthenticator() {
  const { t: base, locale, num } = useI18n();
  const x = TEXT[locale];
  const { persona, epoch } = useSession();
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<{ code: string; until: number } | null>(null);
  const [left, setLeft] = useState(0);

  const load = useCallback(async () => {
    if (!persona) { setData(null); return; }
    try {
      const r = await api<Code>("demo/authenticator");
      setData({ code: r.code, until: Date.now() + r.valid_for_seconds * 1000 });
    } catch {
      setData(null);
    }
  }, [persona]);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch when opened or the persona changes
  useEffect(() => { if (open) void load(); }, [open, load, epoch]);
  useEffect(() => {
    if (!open || !data) return;
    const id = window.setInterval(() => {
      const s = Math.max(0, Math.ceil((data.until - Date.now()) / 1000));
      setLeft(s);
      if (s === 0) void load();
    }, 500);
    return () => window.clearInterval(id);
  }, [open, data, load]);

  if (!persona) return null;
  return (
    <aside data-testid="demo-authenticator" className="mx-3 mt-2 rounded-md border border border-line-strong bg-sand px-3 py-0.5 text-sm lg:mx-8">
      <button type="button" data-testid="demo-authenticator-toggle" onClick={() => setOpen((v) => !v)} className="inline-flex min-h-8 items-center text-[0.8125rem] gap-1.5 font-semibold text-saffron-ink">
        {open ? x.hide : x.show}
      </button>
      {open ? (
        <div className="mt-1">
          <p className="font-semibold">{x.title}</p>
          <p className="text-xs text-muted">{x.note}</p>
          {data ? (
            <p className="mt-1 flex items-baseline gap-3">
              <span className="text-xs text-muted">{x.code}</span>
              <span data-testid="demo-authenticator-code" className="mono rounded-lg bg-surface px-2 text-2xl tracking-[0.2em]">{data.code}</span>
              <span data-testid="demo-authenticator-left" className="text-xs text-muted">{num(x.seconds.replace("{s}", String(left)))}</span>
            </p>
          ) : <p className="text-xs text-muted">{base("common.loading")}</p>}
        </div>
      ) : null}
    </aside>
  );
}
