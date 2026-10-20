import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

/** A designed empty state: an icon, one sentence, and where it helps the next action. */
export function EmptyState({ icon, title, children, testId }: { icon: IconName; title: string; children?: ReactNode; testId?: string }) {
  return (
    <div className="empty" data-testid={testId}>
      <span className="grid h-11 w-11 place-items-center rounded-md bg-sand text-ink"><Icon name={icon} size={22} /></span>
      <p className="font-semibold text-ink">{title}</p>
      {children ? <div className="max-w-sm text-sm">{children}</div> : null}
    </div>
  );
}

/** Loading placeholder blocks (no text, so nothing to translate); the status role announces it once. */
export function Skeleton({ rows = 2, label }: { rows?: number; label: string }) {
  return (
    <div role="status" aria-label={label} className="flex flex-col gap-3">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="card flex flex-col gap-2">
          <div className="skeleton h-4 w-1/3" />
          <div className="skeleton h-3 w-full" />
          <div className="skeleton h-3 w-2/3" />
        </div>
      ))}
    </div>
  );
}

export function PageTitle({ id, eyebrow, children, sub }: { id: string; eyebrow?: string; children: ReactNode; sub?: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
      <h1 id={id} className="h1 font-display">{children}</h1>
      {sub ? <p className="lede">{sub}</p> : null}
    </div>
  );
}
