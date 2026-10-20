"use client";

import { useRef, useState } from "react";
import { westernDigits } from "@/lib/display";

/**
 * Six large digit boxes in front of ONE real input: typing, pasting, autofill (one-time-code) and screen readers all
 * work on the input; the boxes only show it. Digits are always Western (authenticator codes are never localised).
 */
export function CodeBoxes({ value, onChange, label, testId = "totp-input" }: { value: string; onChange: (v: string) => void; label: string; testId?: string }) {
  const ref = useRef<HTMLInputElement>(null);
  const [focus, setFocus] = useState(false);
  const cells = Array.from({ length: 6 }, (_, i) => value[i] ?? "");
  return (
    <label className="flex flex-col gap-2">
      <span className="text-sm font-semibold">{label}</span>
      <span className="relative inline-flex gap-1.5 self-start" onClick={() => ref.current?.focus()}>
        {cells.map((d, i) => (
          <span key={i} className="codebox" data-filled={d !== ""} data-active={focus && i === Math.min(value.length, 5)} aria-hidden="true">{d}</span>
        ))}
        <input
          ref={ref}
          data-testid={testId}
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="[0-9]{6}"
          maxLength={6}
          value={value}
          onFocus={() => setFocus(true)}
          onBlur={() => setFocus(false)}
          onChange={(e) => onChange(westernDigits(e.target.value).replace(/\D/g, "").slice(0, 6))}
          className="absolute inset-0 h-full w-full cursor-text bg-transparent text-transparent caret-transparent opacity-0 focus:opacity-0"
        />
      </span>
    </label>
  );
}
