"""The payee a stored document asks the user to pay, as typed terms (INV-3, INV-4).

Both the agent (to build an ADD_PAYEE proposal) and the executor (to create the payee) read
these terms from the stored ``documents`` row, never from a request or a model: the account is
the HMAC hash and last four digits the reader stored, so the full account number is never held
again (INV-12). A payee created from these terms is always ``untrusted`` and is held by R-PAYEE-
COOLOFF until the cooling off ends."""

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from corridor.contracts.money import CurrencyCode
from corridor.sandbox.models import Payee

# destination region by currency (a region, not a country: ISO 3166 codes)
REGION_OF_CURRENCY = {"AUD": "AU", "QAR": "QA", "MYR": "MY", "CNY": "CN", "USD": "US", "NPR": "NP"}
ACCOUNT_REF_PREFIX = "doc_acct_"


MAX_NAME_CHARS = 40
NAME_NOTE = {
    "en": "Name taken from document, not verified",
    "ne": "नाम कागजातबाट लिइएको, प्रमाणित छैन",
    "ne_latn": "Naam kagajat bata liieko, pramanit chhaina",
}


def document_label(masked_account: str) -> str:
    """The fixed label for a payee that came from a document (never its raw name)."""
    return f"Payee from document {masked_account}"


def document_names(session: Session, user_id: str) -> dict[str, str]:
    """payee id -> sanitised name for the user's payees that came from a document, message or model.

    For deterministic code only (matching what the user typed, and the confirmation card). It
    must never be put in a prompt: models get the fixed label from ``get_payees``."""
    rows = session.execute(
        select(Payee.id, Payee.display_name).where(
            Payee.user_id == user_id,
            (Payee.provenance != "system") | (Payee.added_via == "document"),
        )
    )
    return {pid: name for pid, name in rows}


_NAME_OK = re.compile(r"[A-Za-z0-9\u0900-\u097F .,'&()/-]")


def sanitize_payee_name(raw: str) -> tuple[str, bool]:
    """A document-derived payee name made safe to store and show (INV-3).

    Latin letters, digits, Devanagari, spaces and basic punctuation only, at most 40 characters.
    Anything else is dropped (``changed`` is True and the executor records it in the ledger). An
    empty result means the name is rejected.

    The name is untrusted text. It is shown to the user by deterministic code and never put in any
    planner or composer prompt; the length and alphabet limits make it a poor carrier for an
    injection anyway."""
    kept = "".join(ch for ch in raw if _NAME_OK.fullmatch(ch))
    clean = re.sub(r"\s+", " ", kept).strip()[:MAX_NAME_CHARS].strip()
    return clean, clean != raw.strip()


@dataclass(frozen=True)
class DocPayeeTerms:
    """Name, account hash, last four digits and currency taken from one stored document."""

    name: str
    account_hash: str
    last4: str
    currency: str
    name_sanitised: bool = False
    """True when the name in the document had to be cut or cleaned (logged to the ledger)."""

    @property
    def account_ref(self) -> str:
        """The placeholder carried in a proposal instead of the account number."""
        return ACCOUNT_REF_PREFIX + self.account_hash[:16]

    @property
    def region(self) -> str:
        return REGION_OF_CURRENCY[self.currency]


def payee_terms(fields: dict[str, Any]) -> DocPayeeTerms | None:
    """Terms for a new payee from a document's stored fields, or None when anything is missing.

    An invoice that matched a stored invoice is never a source: its beneficiary is the stored
    one and a different account is handled by R-TAINT-PAYEE and R-INVOICE-BENEFICIARY, not by
    saving a payee."""
    kind = fields.get("kind")
    if kind == "invoice":
        if fields.get("matched_invoice_id"):
            return None
        name = fields.get("beneficiary_name") or fields.get("issuer")
        acct_hash, last4, ccy = (
            fields.get("beneficiary_hash"),
            fields.get("beneficiary_last4"),
            fields.get("currency"),
        )
    elif kind == "message":
        name = fields.get("sender_label")
        acct_hash, last4, ccy = (
            fields.get("mentioned_hash"),
            fields.get("mentioned_last4"),
            fields.get("requested_currency"),
        )
    else:
        return None
    if not (isinstance(name, str) and name.strip() and isinstance(acct_hash, str) and acct_hash):
        return None
    if not (
        isinstance(last4, str) and len(last4) == 4 and last4.isdigit() and isinstance(ccy, str)
    ):
        return None
    try:
        CurrencyCode(ccy)
    except ValueError:
        return None
    if ccy not in REGION_OF_CURRENCY:
        return None
    clean, changed = sanitize_payee_name(name)
    if not clean:
        return None
    return DocPayeeTerms(clean, acct_hash, last4, ccy, changed)
