"""Deterministic matching of a read invoice to the user's stored invoices (INV-3, INV-4).

Free text from a document (the issuer name) is matched to a stored invoice by code and then discarded:
the planner only ever sees the stored invoice id, never the document's words. The stored beneficiary is
the trusted record; the document's account is compared to it by HMAC hash.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from corridor.sandbox import vault
from corridor.sandbox.models import Invoice, Payee


@dataclass(frozen=True)
class InvoiceMatch:
    """Which stored invoice a document belongs to and how it compares to the stored record."""

    invoice_id: str | None
    ambiguous: bool = False
    amount_matches: bool | None = None
    account_matches: bool | None = None


def _tokens(text: str) -> set[str]:
    base = re.sub(r"\(.*?\)", " ", text)
    return {t for t in re.findall(r"[^\W_]+", base.casefold()) if len(t) > 2}


def match_invoice(
    session: Session,
    user_id: str,
    *,
    issuer: str | None,
    amount_minor: int | None,
    currency: str | None,
    account_digits: str | None,
    secrets_dir: Path | None = None,
) -> InvoiceMatch:
    """Match by issuer words (and amount when several invoices share an issuer)."""
    doc_tokens = _tokens(issuer or "")
    open_invoices = session.scalars(
        select(Invoice).where(Invoice.user_id == user_id, Invoice.status == "open")
    ).all()
    same_issuer = [i for i in open_invoices if (t := _tokens(i.issuer)) and t <= doc_tokens]
    if len(same_issuer) > 1:
        by_amount = [
            i for i in same_issuer if i.amount_minor == amount_minor and i.currency == currency
        ]
        same_issuer = by_amount if len(by_amount) == 1 else same_issuer
    if not same_issuer:
        return InvoiceMatch(None)
    if len(same_issuer) > 1:
        return InvoiceMatch(None, ambiguous=True)
    inv = same_issuer[0]
    amount_ok = (
        None
        if amount_minor is None
        else (inv.amount_minor == amount_minor and inv.currency == currency)
    )
    account_ok = None
    if account_digits is not None:
        payee = session.scalars(
            select(Payee).where(Payee.id == inv.beneficiary_payee_id, Payee.user_id == user_id)
        ).first()
        account_ok = (
            payee is not None
            and vault.account_hash(account_digits, secrets_dir) == payee.account_no_hash
        )
    return InvoiceMatch(inv.id, False, amount_ok, account_ok)
