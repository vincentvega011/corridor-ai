"""Read, check and store documents: the quarantined reader pipeline (docs/spec/modules.md 5.5).

``read_upload`` handles an image or PDF, ``read_pasted`` handles pasted third party text. Both end in a
``documents`` row that is always ``untrusted``, with the signals that made it suspicious:

(a) deterministic instruction-like patterns in all extracted text (en, ne, romanised ne);
(b) the reader model's own flag for text addressed to an AI or instructions to change payment;
(c) the document's beneficiary account differs from the stored beneficiary of the matching invoice;
(d) "updated bank details" style claims.

``injection_suspected`` is true if any of them fired. The reader can only add caution: a fooled reader
that reports nothing still faces (a), (c) and (d), which do not depend on it.
"""

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from corridor.contracts.documents import InvoiceDoc, MessageDoc
from corridor.llm import Gateway
from corridor.reader.match import match_invoice
from corridor.reader.model import (
    RawReaderOutput,
    build_invoice,
    build_message,
    digits_only,
    first_account_in,
    image_messages,
    run_reader,
    text_messages,
)
from corridor.reader.patterns import load_patterns
from corridor.reader.summary import typed_summary
from corridor.reader.upload import process_upload
from corridor.sandbox import vault
from corridor.sandbox.models import DocumentRow


@dataclass(frozen=True)
class ReadResult:
    """A stored document, described by typed values only."""

    doc_hash: str
    kind: str
    injection_suspected: bool
    signals: tuple[str, ...]
    fields: dict[str, Any]
    summary: str


def load_document(engine: Engine, doc_hash: str, user_id: str) -> ReadResult | None:
    """The user's own stored document, or None (another user's document looks missing)."""
    with Session(engine) as s:
        row = s.get(DocumentRow, (doc_hash, user_id))
        if row is None:
            return None
        signals, fields = list(row.signals), dict(row.fields)
        return ReadResult(
            row.doc_hash, row.kind, row.injection_suspected, tuple(signals), fields,
            typed_summary(row.doc_hash, fields, row.injection_suspected, signals),
        )  # fmt: skip


def _text_of(doc: InvoiceDoc | MessageDoc, raw: RawReaderOutput, extra: str = "") -> str:
    parts: list[str] = [extra, *raw.notable_text]
    for name in type(doc).model_fields:
        v = getattr(doc, name)
        for item in v if isinstance(v, tuple) else (v,):
            if hasattr(item, "value") and isinstance(item.value, str):
                parts.append(item.value)
    return "\n".join(p for p in parts if p)


def _fields_invoice(
    doc: InvoiceDoc, raw: RawReaderOutput, session: Session, user_id: str, secrets_dir: Path | None
) -> tuple[dict[str, Any], list[str]]:
    digits = digits_only(doc.beneficiary_account.value if doc.beneficiary_account else None)
    # keep the full digits of the number as printed for matching only; the stored form is last 4 + HMAC
    printed = digits_only(raw.invoice.beneficiary_account if raw.invoice else None)
    digits = printed or digits
    account_text = bool(doc.beneficiary_account)  # an account was printed, even if unreadable
    amount = doc.amount.value if doc.amount else None
    match = match_invoice(
        session, user_id,
        issuer=doc.issuer.value, amount_minor=amount.amount_minor if amount else None,
        currency=amount.currency.value if amount else None,
        account_digits=digits if digits is not None else ("" if account_text else None),
        secrets_dir=secrets_dir,
    )  # fmt: skip
    signals: list[str] = []
    if match.account_matches is False:
        signals.append("beneficiary_differs")
    if match.amount_matches is False:
        signals.append("amount_differs")
    if raw.contains_payment_instructions and match.account_matches is not True:
        signals.append(
            "payment_instruction_unmatched"
        )  # pay-somewhere text with no matching account
    fields: dict[str, Any] = {
        "kind": "invoice",
        "issuer": doc.issuer.value,
        "amount_minor": amount.amount_minor if amount else None,
        "currency": amount.currency.value if amount else None,
        "due_date": doc.due_date.value.isoformat() if doc.due_date else None,
        "beneficiary_name": doc.beneficiary_name.value if doc.beneficiary_name else None,
        "beneficiary_last4": digits[-4:] if digits else None,
        "beneficiary_hash": vault.account_hash(digits, secrets_dir) if digits else None,
        "reference": doc.reference.value if doc.reference else None,
        "matched_invoice_id": match.invoice_id,
        "ambiguous_invoice": match.ambiguous,
        "beneficiary_matches_stored": match.account_matches,
        "amount_matches_stored": match.amount_matches,
        "notable_text": [n[:120] for n in raw.notable_text],
    }  # fmt: skip
    return fields, signals


def _fields_message(
    doc: MessageDoc, raw: RawReaderOutput, secrets_dir: Path | None
) -> dict[str, Any]:
    digits = digits_only(doc.mentioned_account.value if doc.mentioned_account else None)
    amount = doc.requested_amount.value if doc.requested_amount else None
    return {
        "kind": "message",
        "sender_label": doc.sender_label.value,
        "claimed_relationship": doc.claimed_relationship.value if doc.claimed_relationship else None,
        "requested_amount_minor": amount.amount_minor if amount else None,
        "requested_currency": amount.currency.value if amount else None,
        "mentioned_last4": digits[-4:] if digits else None,
        "mentioned_hash": vault.account_hash(digits, secrets_dir) if digits else None,
        "urgency_markers": [u.value for u in doc.urgency_markers],
        "notable_text": [n[:120] for n in raw.notable_text],
    }  # fmt: skip


def _finish(
    engine: Engine, user_id: str, doc_hash: str, raw: RawReaderOutput, now: datetime,
    secrets_dir: Path | None, scan_extra: str = "", account_override: str | None = None,
) -> ReadResult:  # fmt: skip
    patterns = load_patterns()
    signals: list[str] = []
    with Session(engine) as s, s.begin():
        doc: InvoiceDoc | MessageDoc
        if raw.doc_type == "invoice" and raw.invoice is not None:
            inv_doc = build_invoice(
                raw.invoice, doc_hash, raw.injection_suspected, raw.contains_payment_instructions
            )
            fields, extra_signals = _fields_invoice(inv_doc, raw, s, user_id, secrets_dir)
            doc = inv_doc
        elif raw.doc_type == "message" and raw.message is not None:
            msg_doc = build_message(
                raw.message,
                doc_hash,
                raw.injection_suspected,
                raw.contains_payment_instructions,
                account_override,
            )
            fields, extra_signals = _fields_message(msg_doc, raw, secrets_dir), []
            doc = msg_doc
        else:
            raise ValueError("the reader returned no document of its declared type")
        signals += patterns.scan(_text_of(doc, raw, scan_extra))
        if raw.injection_suspected:
            signals.append("model_flag")
        signals += extra_signals
        signals = list(dict.fromkeys(signals))
        flagged = any(sig != "amount_differs" for sig in signals)
        s.execute(
            insert(DocumentRow)
            .values(
                doc_hash=doc_hash, user_id=user_id, kind=fields["kind"], injection_suspected=flagged,
                signals=signals, fields=fields, provenance="untrusted", created_at=now,
            )
            .on_conflict_do_nothing()
        )  # fmt: skip
    return ReadResult(
        doc_hash, fields["kind"], flagged, tuple(signals), fields,
        typed_summary(doc_hash, fields, flagged, signals),
    )  # fmt: skip


async def read_upload(
    gateway: Gateway, engine: Engine, user_id: str, data: bytes, now: datetime,
    secrets_dir: Path | None = None, upload_dir: Path | None = None,
) -> ReadResult:  # fmt: skip
    """Process an uploaded image or PDF. Raises ``UploadError`` for a refused file."""
    # decoding and rasterising are CPU and memory heavy: keep them off the event loop
    stored = await asyncio.to_thread(process_upload, data, upload_dir, user_id)
    existing = load_document(engine, stored.doc_hash, user_id)
    if existing is not None:
        return existing
    raw = await run_reader(
        gateway, image_messages(stored.png, "invoice or chat message", stored.doc_hash)
    )
    return _finish(engine, user_id, stored.doc_hash, raw, now, secrets_dir)


async def read_pasted(
    gateway: Gateway, engine: Engine, user_id: str, text: str, now: datetime,
    secrets_dir: Path | None = None,
) -> ReadResult:  # fmt: skip
    """Process pasted third party text as a message document (text mode of the reader)."""
    doc_hash = hashlib.sha256(("pasted:" + text).encode("utf-8")).hexdigest()
    existing = load_document(engine, doc_hash, user_id)
    if existing is not None:
        return existing
    raw = await run_reader(gateway, text_messages(text))
    raw = raw.model_copy(update={"doc_type": "message", "invoice": None})
    if raw.message is None:
        from corridor.reader.model import RawMessage

        raw = raw.model_copy(update={"message": RawMessage()})
    return _finish(
        engine, user_id, doc_hash, raw, now, secrets_dir, scan_extra=text,
        account_override=first_account_in(text),
    )  # fmt: skip


def flagged_hashes(engine: Engine, user_id: str) -> set[str]:
    """Hashes of the user's documents flagged as injection suspected."""
    with Session(engine) as s:
        return set(
            s.scalars(
                select(DocumentRow.doc_hash).where(
                    DocumentRow.user_id == user_id, DocumentRow.injection_suspected.is_(True)
                )
            )
        )
