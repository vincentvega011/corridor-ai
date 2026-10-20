"use client";

import { useI18n } from "@/lib/i18n";
import { Icon, type IconName } from "./Icon";

export type TrustKind = "verified" | "typed" | "document" | "held";
const ICON: Record<TrustKind, IconName> = { verified: "shield-check", typed: "pencil", document: "document", held: "clock-lock" };

/**
 * Where a value came from, in one glance. Shape and icon differ for each kind, and the label is always written
 * out, so the meaning never depends on colour alone: Verified (shield, solid pill), Typed by you (pencil, outlined
 * pill), From document (page, dashed square chip, untrusted), Held (clock and lock, hatched).
 */
export function TrustChip({ kind, label, testId }: { kind: TrustKind; label?: string; testId?: string }) {
  const { t } = useI18n();
  return (
    <span className={`trust trust-${kind}`} data-trust={kind} data-testid={testId}>
      <Icon name={ICON[kind]} size={15} />
      {label ?? t(`trust.${kind}`)}
    </span>
  );
}

/** The bank's own provenance codes mapped onto the four kinds (a payee made from a document is untrusted). */
export function trustOfProvenance(p: string, verified: boolean): TrustKind {
  if (verified) return "verified";
  if (p === "user_typed" || p === "user_confirmed") return "typed";
  if (p === "system") return "verified";
  return "document";
}
