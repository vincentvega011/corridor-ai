"""The typed summary of a document that the planner may see (INV-3).

It is built only from enums, amounts, masked accounts (last 4) and booleans. Issuer names, beneficiary
names, references and every other piece of free text from the document are deliberately absent: they are
matched to stored records by code and never shown to a model.
"""

from typing import Any

from corridor.contracts.money import CurrencyCode
from corridor.formatting import format_minor

KNOWN_SIGNALS = (
    "pattern:ignore_previous", "pattern:system_tag", "pattern:addressed_to_ai",
    "pattern:pay_instruction", "pattern:skip_confirm", "pattern:bank_update",
    "model_flag", "beneficiary_differs", "amount_differs", "payment_instruction_unmatched",
)  # fmt: skip


def doc_label(doc_hash: str) -> str:
    return f"d_{doc_hash[:4]}"


def _money(minor: Any, currency: Any) -> str | None:
    if (
        isinstance(minor, int)
        and not isinstance(minor, bool)
        and currency in CurrencyCode._value2member_map_
    ):
        return format_minor(minor, CurrencyCode(currency))
    return None


def typed_summary(
    doc_hash: str, fields: dict[str, Any], injection: bool, signals: list[str]
) -> str:
    """One line describing the document with typed values only."""
    kind = fields.get("kind", "document")
    parts = [f"document {doc_label(doc_hash)}: {kind}"]
    if kind == "invoice":
        amt = _money(fields.get("amount_minor"), fields.get("currency"))
        if amt:
            parts.append(f"amount {amt} (untrusted)")
        if fields.get("beneficiary_last4"):
            note = "untrusted"
            if fields.get("beneficiary_matches_stored") is False:
                note += ", differs from stored beneficiary"
            elif fields.get("beneficiary_matches_stored") is True:
                note += ", equals stored beneficiary"
            parts.append(f"beneficiary ****{fields['beneficiary_last4']} ({note})")
        if fields.get("amount_matches_stored") is False:
            parts.append("amount differs from stored invoice")
        if fields.get("matched_invoice_id"):
            parts.append(f"matches stored invoice {fields['matched_invoice_id']}")
    else:
        req = _money(fields.get("requested_amount_minor"), fields.get("requested_currency"))
        parts.append(f"requests {req} (untrusted)" if req else "requests no amount")
        if fields.get("mentioned_last4"):
            parts.append(f"mentions account ****{fields['mentioned_last4']} (untrusted)")
    parts.append(f"injection_suspected: {str(injection).lower()}")
    kept = [s for s in signals if s in KNOWN_SIGNALS]
    if kept:
        parts.append("signals: " + ", ".join(kept))
    return ", ".join(parts)
