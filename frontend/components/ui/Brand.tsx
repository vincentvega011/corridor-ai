/** The product mark: two lanes that meet (a corridor) on a black tile, with a waypoint. */
export function BrandMark({ size = 34 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" aria-hidden="true" focusable="false">
      <rect width="40" height="40" rx="6" fill="var(--color-pine)" />
      <path d="M9 29c5-1 7-6 11-10s8-5 11-10" fill="none" stroke="var(--color-on-solid)" strokeWidth="3" strokeLinecap="round" />
      <path d="M9 21c3 0 5 2 7 4" fill="none" stroke="var(--color-on-solid)" strokeOpacity="0.55" strokeWidth="3" strokeLinecap="round" />
      <circle cx="31" cy="9.5" r="3.2" fill="var(--color-on-solid)" />
    </svg>
  );
}
