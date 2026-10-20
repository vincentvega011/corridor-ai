import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

export type TimelineState = "done" | "current" | "current-pine" | "next";
export interface TimelineStep { key: string; state: TimelineState; icon: IconName; title: ReactNode; body?: ReactNode; testId?: string }

/** A vertical stepper: filled dot = done, solid colour = where we are, dashed = what comes next. */
export function Timeline({ steps, label }: { steps: TimelineStep[]; label?: string }) {
  return (
    <ol className="tl" aria-label={label}>
      {steps.map((s) => (
        <li key={s.key} data-testid={s.testId} data-state={s.state}>
          <span className="tl-dot" data-state={s.state}><Icon name={s.state === "done" ? "check" : s.icon} size={15} /></span>
          <div className="min-h-[1.9rem]">
            <p className="text-[0.9375rem] font-semibold leading-snug">{s.title}</p>
            {s.body ? <div className="mt-0.5 text-sm text-muted">{s.body}</div> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}
