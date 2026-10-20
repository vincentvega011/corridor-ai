"""Synthetic NLU items are marked and kept apart; the human sheet import path works (P3-4)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
NLU = ROOT / "eval" / "nlu"
sys.path.insert(0, str(NLU))

import import_human  # noqa: E402


def rows(name: str) -> list[dict]:
    return [
        json.loads(x) for x in (NLU / name).read_text(encoding="utf-8").splitlines() if x.strip()
    ]


def test_synthetic_items_are_marked_eval_only_and_do_not_copy_the_human_test_set() -> None:
    synthetic = rows("synthetic.jsonl")
    assert len(synthetic) >= 100
    assert all(r["synthetic"] is True and r["eval_only"] is True for r in synthetic)
    assert all(r["reviewed"] is False for r in synthetic)
    held = {r["text"].lower() for r in rows("test.jsonl")} | {
        r["text"].lower() for r in rows("candidates.jsonl")
    }
    assert not any(r["text"].lower() in held for r in synthetic)
    assert len({r["text"].lower() for r in synthetic}) == len(synthetic)


def test_the_human_template_has_the_three_columns() -> None:
    assert (NLU / "human_template.csv").read_text(encoding="utf-8").splitlines()[
        0
    ] == "message,meaning,language"


def test_a_filled_sheet_becomes_unreviewed_test_items(tmp_path: Path) -> None:
    sheet = tmp_path / "filled.csv"
    sheet.write_text(
        "message,meaning,language\n"
        "#EXAMPLE skip,x,en\n"
        "mero bhai lai 7 hajar pathaideu parsi samma,send 7000 NPR to my younger brother,ne_latn\n"
        "yo khata 12345678901 ma pathau,send money,ne_latn\n"
        "kehi,something,fr\n",
        encoding="utf-8",
    )
    items, problems = import_human.import_sheet(sheet)
    assert len(items) == 1 and len(problems) == 2
    item = items[0]
    assert item["reviewed"] is False and item["human_written"] is True
    assert item["intent"] == "send_money"
    assert item["slots"] == {"amount": 700000, "payee_ref": "younger_brother", "currency": "NPR"}


def test_the_parser_output_kept_for_the_officer_trace_masks_account_like_digits() -> None:
    from corridor.contracts.llm_outputs import ParserOutput
    from corridor.nlu.parser import _masked_raw

    out = ParserOutput.model_validate(
        {
            "language": "en",
            "intent": "send_money",
            "slots": {"payee_ref": "Rajan 927364510055", "amount": 4000},
            "confidence_bps": 9000,
        }
    )
    raw = _masked_raw(out)
    assert "927364510055" not in raw and "send_money" in raw
