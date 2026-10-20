/**
 * A slim progress ring for the 120 s confirmation grant. Drawn with SVG attributes only (the strict CSP forbids
 * inline style attributes). The text inside is the seconds left; role="timer" gives assistive technology the value.
 */
export function CountdownRing({ left, total, label }: { left: number; total: number; label: string }) {
  const r = 17;
  const c = 2 * Math.PI * r;
  const frac = total > 0 ? Math.max(0, Math.min(1, left / total)) : 0;
  const tone = left <= 10 ? "var(--color-deny)" : left <= 30 ? "var(--color-saffron)" : "var(--color-pine)";
  return (
    <span role="timer" aria-label={label} className="relative inline-grid h-11 w-11 shrink-0 place-items-center" data-testid="card-ring">
      <svg width="44" height="44" viewBox="0 0 44 44" className="-rotate-90" aria-hidden="true" focusable="false">
        <circle cx="22" cy="22" r={r} fill="none" stroke="var(--color-line)" strokeWidth="3.5" />
        <circle className="ring-arc" cx="22" cy="22" r={r} fill="none" stroke={tone} strokeWidth="3.5" strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - frac)} />
      </svg>
      <span className="num absolute text-[0.8125rem] font-bold" aria-hidden="true">{left}</span>
    </span>
  );
}
