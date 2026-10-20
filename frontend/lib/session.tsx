"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import type { ClockResponse } from "@/lib/contracts";

export type Persona = "usr_priya" | "usr_ramesh" | "usr_anil";
export const PERSONAS: Persona[] = ["usr_priya", "usr_ramesh", "usr_anil"];

interface SessionCtx {
  persona: Persona | null;
  ready: boolean;
  clock: ClockResponse | null;
  choose: (p: Persona) => Promise<void>;
  /** Bumped when the persona changes, so screens reload their data. */
  epoch: number;
}
const Ctx = createContext<SessionCtx | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [persona, setPersona] = useState<Persona | null>(null);
  const [ready, setReady] = useState(false);
  const [clock, setClock] = useState<ClockResponse | null>(null);
  const [epoch, setEpoch] = useState(0);

  useEffect(() => {
    void (async () => {
      try {
        const s = (await (await fetch("/api/session", { cache: "no-store" })).json()) as { persona: Persona | null };
        setPersona(s.persona);
      } catch {
        setPersona(null);
      }
      try {
        const res = await fetch("/api/bff/clock", { cache: "no-store" });
        if (res.ok) setClock((await res.json()) as ClockResponse);
      } catch {
        setClock(null);
      }
      setReady(true);
    })();
  }, []);

  const choose = useCallback(async (p: Persona) => {
    const res = await fetch("/api/session", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ persona: p }),
    });
    if (res.ok) {
      setPersona(p);
      setEpoch((e) => e + 1);
    }
  }, []);

  return <Ctx.Provider value={{ persona, ready, clock, choose, epoch }}>{children}</Ctx.Provider>;
}

export function useSession(): SessionCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSession outside SessionProvider");
  return v;
}
