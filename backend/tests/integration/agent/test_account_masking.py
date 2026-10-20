"""INV-12: account-like numbers never reach a prompt (P3-3). Asserted on the requests a gateway recorded."""

import re

import pytest

from agent_helpers import collect
from corridor.agent.service import AgentService
from corridor.llm.guard import mask_account_numbers

CASES = {
    "au_bsb_account": (
        "Pay Mr Chen AUD 50, BSB 062-000 account 12345678",
        ["062-000", "062000", "12345678"],
    ),
    "np_15_digit": (
        "Ama lai 40 hajar pathau khata 012345678901234",
        ["012345678901234", "345678901234"],
    ),
    "np_16_digit_spaced": (
        "Send to 0123 4567 8901 2345 AUD 40",
        ["0123 4567 8901 2345", "0123456789012345", "89012345"],
    ),
    "cny_card": (
        "Pay 6222 0212 3456 7890 CNY 100",
        ["6222 0212 3456 7890", "6222021234567890", "34567890"],
    ),
    "six_digits": ("What did I send to account 482913", ["482913"]),
}
RUN = re.compile(r"\d(?:[ \-]?\d){5,}")


@pytest.mark.parametrize("name", list(CASES))
async def test_no_account_like_digits_reach_any_prompt(service: AgentService, name: str) -> None:
    text, needles = CASES[name]
    await collect(service.chat("usr_priya", text))
    gw = service.deps.gateway
    seen = [m["content"] for _, ms in gw.calls for m in ms if isinstance(m.get("content"), str)]  # type: ignore[attr-defined]
    blob = "\n".join(seen)
    for needle in needles:
        assert needle not in blob and re.sub(r"\D", "", needle) not in re.sub(r"[ \-]", "", blob)
    # the user's own words in every prompt (the parser's "Message:" line, the composer's "user_request") have none
    typed = [
        line
        for part in seen
        for line in part.splitlines()
        if line.startswith("Message:") or '"user_request"' in line
    ]
    for line in typed:
        assert not [m for m in RUN.findall(re.sub(r"\d{4}-\d{2}-\d{2}", "", line)) if "," not in m]


def test_the_mask_is_deterministic_and_leaves_dates_money_and_small_numbers() -> None:
    t = "On 2026-10-10 pay AUD 2,000.00 for 3 days, ref 12-34"
    assert mask_account_numbers(t) == t
    assert mask_account_numbers("acct 062-000 12345678.") == "acct [ACCOUNT]."
    assert mask_account_numbers("0620 0077 1234") == "[ACCOUNT]"
    assert (
        mask_account_numbers("१२३४५६७८९०१२३४") == "[ACCOUNT]"
    )  # Devanagari digits are normalised first
    assert mask_account_numbers("x") == mask_account_numbers("x")


@pytest.mark.parametrize("name", list(CASES))
def test_the_parser_prompt_for_each_case_has_no_account_digits(name: str) -> None:
    from datetime import date

    from corridor.nlu.lang_pack import load_packs
    from corridor.nlu.normalise import normalise
    from corridor.nlu.parser import build_messages

    text, needles = CASES[name]
    packs = load_packs()
    msgs = build_messages(text, normalise(text, date(2026, 10, 10), packs), packs)
    last = msgs[-1]["content"]
    assert last.startswith("Message: ")
    for needle in needles:
        assert needle not in last and re.sub(r"\D", "", needle) not in re.sub(r"[ \-]", "", last)
    assert not [m for m in RUN.findall(last) if "," not in m]


@pytest.mark.parametrize(
    "raw", ["1234,5678", "1234_5678", "1234/5678", "12.34.56.78", "123 456 78"]
)
def test_other_separators_do_not_hide_a_6_to_9_digit_number(raw: str) -> None:
    assert mask_account_numbers(f"pay {raw} now") == "pay [ACCOUNT] now"
