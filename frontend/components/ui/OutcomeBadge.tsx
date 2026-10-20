"use client";

import { useI18n } from "@/lib/i18n";
import { Icon, type IconName } from "./Icon";

export type Outcome = "allow" | "confirm" | "mfa" | "deny";
const ICON: Record<Outcome, IconName> = { allow: "check-circle", confirm: "hand", mfa: "keypad", deny: "octagon-x" };

/** The one badge for a policy decision (allowed, needs confirmation, needs authenticator code, denied). */
export function OutcomeBadge({ level, label, testId }: { level: Outcome; label?: string; testId?: string }) {
  const { t } = useI18n();
  return (
    <span className={`outcome outcome-${level}`} data-outcome={level} data-testid={testId}>
      <Icon name={ICON[level]} size={16} />
      {label ?? t(`outcome.${level}`)}
    </span>
  );
}

/** Map the server's required level or decision string onto a badge. */
export function outcomeOf(level: string | null | undefined): Outcome {
  switch (level) {
    case "mfa":
    case "require_mfa":
      return "mfa";
    case "deny":
    case "denied":
    case "block":
      return "deny";
    case "allow":
    case "allowed":
      return "allow";
    default:
      return "confirm";
  }
}
