"""The deterministic rent relabelling of the fine-tune data (P2-4 part E)."""

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "gen_data", Path(__file__).resolve().parents[3] / "models" / "finetune" / "gen_data.py"
)
gen = importlib.util.module_from_spec(SPEC)  # type: ignore[arg-type]
SPEC.loader.exec_module(gen)  # type: ignore[union-attr]


def target(intent="pay_invoice", **slots):
    return {
        "intent": intent,
        "language": "ne_latn",
        "slots": slots,
        "missing_slots": [],
        "confidence_bps": 9500,
    }


def test_rent_payment_without_an_invoice_word_becomes_send_money():
    t, changed = gen.apply_rent_rule(
        "Mero rent tirna 200 AUD pathauna parcha", target(amount=200, currency="AUD")
    )
    assert changed and t["intent"] == "send_money" and t["missing_slots"] == ["payee_ref"]
    assert t["slots"] == {"amount": 200, "currency": "AUD"} and t["confidence_bps"] == 9500


def test_a_message_that_names_an_invoice_stays_pay_invoice():
    for text in (
        "rent ko invoice pay garna parcha",
        "mero rent ra tuition ko bill ayeko chha",
        "मेरो rent को इनभ्वाइस तिर्नु पर्छ",
    ):
        t, changed = gen.apply_rent_rule(text, target())
        assert not changed and t["intent"] == "pay_invoice"


def test_other_intents_and_non_rent_rows_are_untouched():
    assert gen.apply_rent_rule("rent tirna parcha", target("send_money"))[1] is False
    assert gen.apply_rent_rule("university lai 500 tirna parcha", target(amount=500))[1] is False
