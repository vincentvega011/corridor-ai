"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { OFFICERS } from "@/lib/console/api";
import { Icon } from "@/components/ui/Icon";
import { useI18n } from "@/lib/i18n";

export default function ConsoleLogin() {
  const { tc } = useI18n();
  const router = useRouter();
  const [officer, setOfficer] = useState<string>(OFFICERS[0]);
  const [credential, setCredential] = useState("");
  const [error, setError] = useState(false);
  const [going, setGoing] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(false);
    const res = await fetch("/api/console/session", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ officer, credential }),
    });
    setCredential("");
    if (res.ok) {
      setGoing(true);
      router.replace("/console/queue");
    }
    else setError(true);
  }

  if (going) return null;
  return (
    <form onSubmit={(e) => void submit(e)} className="card card-raised mx-auto mt-12 flex max-w-sm flex-col gap-4 p-6">
      <span className="grid h-10 w-10 place-items-center rounded-lg bg-pine text-on-solid"><Icon name="bank" size={22} /></span>
      <div>
        <h1 className="text-lg font-semibold">{tc("c.login.title")}</h1>
        <p className="mt-1 text-xs text-muted">{tc("c.login.note")}</p>
      </div>
      <label className="field">{tc("c.login.officer")}
        <select data-testid="login-officer" value={officer} onChange={(e) => setOfficer(e.target.value)} className="input mono">
          {OFFICERS.map((o) => <option key={o}>{o}</option>)}
        </select>
      </label>
      <label className="field">{tc("c.login.credential")}
        <input data-testid="login-credential" type="password" autoComplete="current-password" value={credential} onChange={(e) => setCredential(e.target.value)} className="input mono" required />
      </label>
      {error ? <p role="alert" data-testid="login-error" className="text-sm font-semibold text-deny">{tc("c.login.error")}</p> : null}
      <button type="submit" data-testid="login-submit" className="btn btn-primary"><Icon name="lock" size={16} />{tc("c.login.submit")}</button>
    </form>
  );
}
