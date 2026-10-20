"""Turning reader output into typed untrusted documents (docs/spec/modules.md 5.5)."""

from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from corridor.contracts import CurrencyCode, Provenance, Tainted
from corridor.reader.model import (
    RawInvoice,
    RawMessage,
    build_invoice,
    build_message,
    cap,
    digits_only,
    parse_amount,
    parse_date,
    redact_numbers,
)

H = "a" * 64


def all_tainted(doc: object) -> list[Tainted]:  # type: ignore[type-arg]
    out = []
    for name in type(doc).model_fields:  # type: ignore[attr-defined]
        v = getattr(doc, name)
        out += [i for i in (v if isinstance(v, tuple) else (v,)) if isinstance(i, Tainted)]
    return out


def test_free_text_is_cut_to_120_characters() -> None:
    assert len(cap("x" * 500) or "") == 120
    assert cap("  a   b \n c ") == "a b c" and cap("   ") is None and cap(None) is None
    doc = build_invoice(RawInvoice(issuer="U" * 1000, beneficiary_name="N" * 999), H, False, False)
    assert len(doc.issuer.value) == 120 and len(doc.beneficiary_name.value) == 120  # type: ignore[union-attr]


def test_every_field_of_an_invoice_and_a_message_is_untrusted_with_the_document_ref() -> None:
    inv = build_invoice(
        RawInvoice(issuer="Uni", amount="6,500.00", currency="AUD", due_date="2026-10-31",
                   beneficiary_name="Fees", beneficiary_account="9685 1330 4471", reference="INV-1"),
        H, True, True,
    )  # fmt: skip
    msg = build_message(
        RawMessage(sender_label="Dinesh", claimed_relationship="cousin", requested_amount="150,000",
                   requested_currency="NPR", mentioned_account="1", urgency_markers=["urgent", "now"]),
        H, False, False, "919553117730",
    )  # fmt: skip
    for doc in (inv, msg):
        fields = all_tainted(doc)
        assert fields and all(
            t.provenance is Provenance.UNTRUSTED and t.source_ref == H[:16] for t in fields
        )
    assert inv.amount.value.amount_minor == 650_000 and inv.due_date.value == date(2026, 10, 31)  # type: ignore[union-attr]
    assert msg.mentioned_account.value == "919553117730"  # type: ignore[union-attr]


def test_a_forged_trusted_label_cannot_survive_the_document_validators() -> None:
    from corridor.contracts.documents import InvoiceDoc

    forged = InvoiceDoc(
        source_doc_hash=H, issuer=Tainted[str](value="Uni", provenance=Provenance.SYSTEM)
    )
    assert forged.issuer.provenance is Provenance.UNTRUSTED


def test_amounts_are_parsed_by_code_and_exactly() -> None:
    assert parse_amount("6,500.00", "AUD").amount_minor == 650_000  # type: ignore[union-attr]
    assert parse_amount("१,००,०००", "NPR").amount_minor == 10_000_000  # type: ignore[union-attr]
    assert parse_amount("NPR 150,000", "npr").amount_minor == 15_000_000  # type: ignore[union-attr]
    assert parse_amount("12.345", "AUD") is None  # not an exact amount of cents
    for bad in (None, "", "abc", "0", "-5"):
        assert parse_amount(bad, "AUD") is None
    assert parse_amount("100", "XXX") is None and parse_amount("100", None) is None
    assert parse_amount("99999999999999999", "AUD") is None


def test_dates_and_accounts() -> None:
    assert parse_date("2026-10-31") == date(2026, 10, 31) and parse_date("31/10/2026") is None
    assert (
        digits_only("9685 1330 4471") == "968513304471"
        and digits_only("४४७१ १२३४ ५६७८") == "447112345678"
    )
    assert digits_only("ref 12") is None and digits_only(None) is None


def test_long_digit_runs_are_replaced_before_any_text_reaches_the_model() -> None:
    out = redact_numbers("send to 9195 5311 7730 or 919553117730, tel 12345")
    assert "919553117730" not in out and "9195 5311 7730" not in out and "[NUMBER-1]" in out
    assert "12345" in out


@given(st.text(max_size=400), st.lists(st.text(max_size=300), max_size=12))
def test_property_no_text_the_reader_returns_can_become_trusted_or_too_long(
    issuer: str, marks: list[str]
) -> None:
    doc = build_message(
        RawMessage(sender_label=issuer, urgency_markers=marks), H, False, False, None
    )
    for t in all_tainted(doc):
        assert t.provenance is Provenance.UNTRUSTED
        assert not isinstance(t.value, str) or len(t.value) <= 120
    assert CurrencyCode.NPR in CurrencyCode
