"""Normaliser: numbers, currencies, kinship, dates, festivals, conditions, language style."""

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from corridor.contracts import CurrencyCode, LanguageTag
from corridor.nlu.lang_pack import load_packs
from corridor.nlu.normalise import CurrencyStatus, normalise

TODAY = date(2026, 10, 10)  # a Saturday
PACKS = load_packs()


def units(text: str) -> list[Decimal]:
    return [Decimal(a.units) for a in normalise(text, TODAY).amounts]


# --------------------------------------------------------------------------- numbers
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("send 20000 to Ama", [20000]),
        ("20k pathau", [20000]),
        ("1.5k", [1500]),
        ("send 1.5 lakh to Dai", [150000]),
        ("1,00,000 rupiya", [100000]),
        ("1,000,000 rupees", [1000000]),
        ("२०,००० रुपैयाँ", [20000]),
        ("आमालाई २० हजार पठाउ", [20000]),
        ("bis hajar pathau", [20000]),
        ("बीस हजार रुपैयाँ", [20000]),
        ("ek lakh pachas hajar", [150000]),
        ("dui lakh", [200000]),
        ("एक लाख", [100000]),
        ("pachchis hajar", [25000]),
        ("pachis hazaar rs", [25000]),
        ("ek crore", [10000000]),
        ("2 karod", [20000000]),
        ("twenty thousand rupees", [20000]),
        ("one hundred and fifty thousand", [150000]),
        ("5 hajar 5 saya", [5500]),
        ("tin saya pachas rupiya", [350]),
        ("Rs. 5000", [5000]),
        ("Rs.5000", [5000]),
        ("rs5000", [5000]),
        ("NPR 12,500.50", [Decimal("12500.50")]),
        ("500 QAR and 20 hajar", [500, 20000]),
    ],
)
def test_amount_parsing(text: str, expected: list[int | Decimal]) -> None:
    assert units(text) == [Decimal(e) for e in expected]


@pytest.mark.parametrize(
    "text",
    [
        "ma ramro chha",  # chha means "is", not 6
        "ek patak bhetchhu",  # ek means "a"
        "das minute ma aaucha",
        "20 din ma pathau",  # a duration, not an amount
        "2 hapta pachi",
        "in 3 months",
        "25 percent",
        "10% off",
        "send it on 2026-10-31",
        "31 oct",
    ],
)
def test_numbers_that_are_not_amounts(text: str) -> None:
    assert units(text) == []


def test_amount_minor_units_are_integers_and_use_the_currency_exponent() -> None:
    n = normalise("send 12,500.50 NPR to Ama", TODAY)
    a = n.amounts[0]
    assert a.amount_minor == 1_250_050 and a.currency and a.currency.code is CurrencyCode.NPR
    assert not a.exponent_assumed
    b = normalise("20 hajar", TODAY).amounts[0]
    assert b.amount_minor == 2_000_000 and b.exponent_assumed
    c = normalise("0.005 rupees", TODAY).amounts[0]
    assert c.amount_minor is None  # not an integral minor unit


def test_out_of_range_amount_is_flagged_and_has_no_minor_value() -> None:
    a = normalise("send 10 crore 50 lakh to Ama", TODAY).amounts[0]
    assert not a.in_range and a.amount_minor is None


def test_spans_are_offsets_into_the_original_text() -> None:
    text = "  आमालाई  २० हजार   रुपैयाँ पठाउ Suresh"
    n = normalise(text, TODAY)
    for s in [
        n.amounts[0].span,
        n.relations[0].span,
        *(c.span for c in n.currencies),
        *(x.span for x in n.names),
    ]:
        assert text[s.start : s.end] == s.text
    assert n.amounts[0].span.text == "२० हजार"


# --- property: any supported style parses back to the same integer ---------------------------
def indian(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join([*parts, tail])


def western(n: int) -> str:
    return f"{n:,}"


def deva(n: str) -> str:
    return n.translate(str.maketrans("0123456789", "०१२३४५६७८९"))


NE_WORDS = {
    int(v): forms
    for v, forms in __import__("yaml")
    .safe_load(
        open(
            __import__("pathlib").Path(__file__).resolve().parents[4] / "data/lang/ne/numbers.yaml"
        )
    )["words"]
    .items()
}


@settings(max_examples=150)
@given(
    st.integers(min_value=1, max_value=99_999_999),
    st.sampled_from(["plain", "indian", "western", "deva", "devaindian"]),
)
def test_integer_roundtrip_digits(n: int, style: str) -> None:
    text = {
        "plain": str(n),
        "indian": indian(n),
        "western": western(n),
        "deva": deva(str(n)),
        "devaindian": deva(indian(n)),
    }[style]
    assert units(f"send {text} rupiya") == [Decimal(n)]


@settings(max_examples=120)
@given(
    st.integers(min_value=1, max_value=999),
    st.sampled_from(["k", " hajar", " hazar", " हजार", " thousand"]),
)
def test_integer_roundtrip_thousands(n: int, suffix: str) -> None:
    text = f"{deva(str(n)) if suffix == ' हजार' else n}{suffix}"
    assert units(text) == [Decimal(n * 1000)]


@settings(max_examples=100)
@given(st.integers(min_value=1, max_value=99), st.sampled_from([" lakh", " lac", " लाख"]))
def test_integer_roundtrip_lakh(n: int, suffix: str) -> None:
    assert units(f"{n}{suffix}") == [Decimal(n * 100_000)]


@settings(max_examples=100)
@given(st.integers(min_value=1, max_value=9), st.integers(min_value=1, max_value=9))
def test_decimal_lakh_roundtrip(whole: int, frac: int) -> None:
    assert units(f"{whole}.{frac} lakh") == [Decimal(whole * 100_000 + frac * 10_000)]


@settings(max_examples=120)
@given(st.integers(min_value=11, max_value=99), st.data())
def test_number_words_roundtrip(n: int, data: st.DataObject) -> None:
    script = data.draw(st.sampled_from(["latn", "deva"]))
    word = data.draw(st.sampled_from(NE_WORDS[n][script]))
    mult = {"latn": "hajar", "deva": "हजार"}[script]
    assert units(f"{word} {mult}") == [Decimal(n * 1000)]


def test_devanagari_digit_roundtrip_for_every_digit() -> None:
    for d in range(10):
        assert units(f"{deva(str(d))}{deva('5')} rupiya") == [Decimal(d * 10 + 5)]


# --------------------------------------------------------------------------- currency
@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("500 rupiya", "NPR"), ("Rs 500", "NPR"), ("५०० रुपैयाँ", "NPR"), ("500 npr", "NPR"),
        ("500 riyal", "QAR"), ("500 QAR", "QAR"), ("500 ringgit", "MYR"), ("RM 500", "MYR"),
        ("500 yuan", "CNY"), ("500 RMB", "CNY"), ("500 AUD", "AUD"), ("500 us dollar", "USD"),
        ("USD 500", "USD"), ("500 australian dollars", "AUD"),
    ],
)  # fmt: skip
def test_currency_words(text: str, code: str) -> None:
    n = normalise(text, TODAY)
    assert n.currencies and n.currencies[0].status is CurrencyStatus.RESOLVED
    assert n.currencies[0].code is CurrencyCode(code)
    assert n.amounts[0].currency and n.amounts[0].currency.code is CurrencyCode(code)


@settings(max_examples=60)
@given(
    st.integers(min_value=1, max_value=99_999),
    st.sampled_from(["dollar", "dollars", "$", "डलर", "Dollar"]),
    st.booleans(),
)
def test_bare_dollar_is_always_ambiguous_and_never_resolves(
    n: int, word: str, before: bool
) -> None:
    text = f"{word}{n}" if before and word == "$" else (f"{word} {n}" if before else f"{n} {word}")
    out = normalise(text, TODAY)
    assert out.currencies, text
    for c in out.currencies:
        assert c.status is CurrencyStatus.AMBIGUOUS and c.code is None
        assert set(c.options) == {"AUD", "USD"}
    assert all(a.currency is None or a.currency.code is None for a in out.amounts)
    assert all(a.exponent_assumed for a in out.amounts)


def test_unsupported_currencies_are_flagged() -> None:
    n = normalise("send 100 euro to Dai", TODAY)
    assert (
        n.currencies[0].status is CurrencyStatus.UNSUPPORTED
        and n.currencies[0].unsupported_code == "EUR"
    )


# --------------------------------------------------------------------------- kinship and names
@pytest.mark.parametrize(
    ("text", "relation"),
    [
        ("Ama lai pathau", "mother"), ("aama lai", "mother"), ("आमालाई पठाउ", "mother"), ("send to my mom", "mother"),
        ("buwa lai", "father"), ("बुवालाई", "father"), ("dai lai", "elder_brother"), ("दाइलाई", "elder_brother"),
        ("didi ko khata", "elder_sister"), ("bhai lai", "younger_brother"), ("bahini lai", "younger_sister"),
        ("kaka lai", "paternal_uncle"), ("mama lai", "maternal_uncle"), ("sasu lai", "mother_in_law"),
        ("pay the university", "university"), ("gharbeti lai", "landlord"), ("pay my landlord", "landlord"),
    ],
)  # fmt: skip
def test_kinship(text: str, relation: str) -> None:
    n = normalise(text, TODAY)
    assert relation in {r.relation for r in n.relations}


def test_names() -> None:
    assert [x.name for x in normalise("send 500 AUD to Priya", TODAY).names] == ["Priya"]
    assert [x.name for x in normalise("Suresh lai 30 hajar pathau", TODAY).names] == ["Suresh"]
    assert normalise("Send 500 to someone", TODAY).names == ()
    assert normalise("send 100 on Friday", TODAY).names == ()


# --------------------------------------------------------------------------- dates
def one_date(text: str) -> date | None:
    n = normalise(text, TODAY)
    return n.dates[0].date if n.dates else None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("aaja pathau", date(2026, 10, 10)), ("bholi pathau", date(2026, 10, 11)), ("parsi", date(2026, 10, 12)),
        ("today", date(2026, 10, 10)), ("tomorrow", date(2026, 10, 11)), ("भोलि", date(2026, 10, 11)),
        ("by Friday", date(2026, 10, 16)), ("by next friday", date(2026, 10, 23)), ("sukrabar samma", date(2026, 10, 16)),
        ("2026-10-31", date(2026, 10, 31)), ("31 oct", date(2026, 10, 31)), ("October 31", date(2026, 10, 31)),
        ("5 jan", date(2027, 1, 5)), ("31/10/2026", date(2026, 10, 31)), ("1 march 2027", date(2027, 3, 1)),
        ("arko mahina", date(2026, 11, 30)), ("yo hapta", date(2026, 10, 16)), ("next month", date(2026, 11, 30)),
        ("अक्टोबर ३१", date(2026, 10, 31)),
    ],
)  # fmt: skip
def test_dates(text: str, expected: date) -> None:
    assert one_date(text) == expected


def test_dates_mark_past_and_ambiguity_and_do_not_become_amounts() -> None:
    assert normalise("hijo", TODAY).dates[0].is_past
    assert normalise("3/4/2026", TODAY).dates[0].ambiguous
    assert not normalise("31/10/2026", TODAY).dates[0].ambiguous
    assert normalise("send 500 by 31 oct", TODAY).amounts[0].units == "500"
    assert [a.units for a in normalise("31 oct ma 6500 AUD", TODAY).amounts] == ["6500"]


# --------------------------------------------------------------------------- festivals
def fest(text: str, today: date = TODAY):
    return normalise(text, today).festivals


def test_festival_lookup_before_and_for() -> None:
    """ "Before Dashain" is the Tika day (Vijaya Dashami), "before Dashain starts" is Ghatasthapana;
    the same main day rule applies to Tihar (Laxmi Puja) and the others."""
    tika = date(2026, 10, 21)
    for text in (
        "dashain bhanda agadi 30 hajar pathau",
        "before Dashain send 5000",
        "dashain aghi pathau",
        "दशैँ भन्दा अगाडि पठाउ",
        "दशैंभन्दा अगाडि पठाउ",
        "dashain ko lagi paisa pathau",
    ):
        f = fest(text)[0]
        assert (f.festival_id, f.date, f.verified) == ("dashain", tika, True), text
    assert fest("dashain bhanda agadi")[0].deadline_kind == "before_festival"
    assert fest("dashain ko lagi")[0].deadline_kind == "for_festival"
    start = date(2026, 10, 11)
    for text in (
        "before Dashain starts",
        "dashain suru huna aghi",
        "ghatasthapana aghi",
        "घटस्थापना अघि",
        "dashain begins",
    ):
        f = fest(text)[0]
        assert (f.festival_id, f.date) == ("dashain_start", start), text
    assert fest("tihar bhanda agadi")[0].date == date(2026, 11, 8)
    assert fest("tihar ko lagi")[0].date == date(2026, 11, 8)
    assert fest("bhai tika ko lagi")[0].date == date(2026, 11, 11)
    assert fest("chhath ko lagi")[0].date == date(2026, 11, 15)
    assert fest("lhosar ko lagi")[0].date == date(2026, 12, 30)
    assert fest("naya barsa bhanda agadi")[0].date == date(2027, 4, 14)


def test_festival_after_the_date_rolls_to_next_year_and_unverified_is_unknown() -> None:
    later = fest("for dashain", date(2026, 10, 25))[0]
    assert later.date == date(2027, 10, 10) and later.year == 2027
    unknown = fest("chhath ko lagi", date(2026, 11, 16))[0]
    assert unknown.date is None and unknown.verified is False  # 2027 Chhath has no verified date


def test_every_verified_festival_date_has_a_known_source() -> None:
    import yaml

    from corridor.config import REPO_ROOT

    cfg = yaml.safe_load((REPO_ROOT / "data/calendar/festivals.yaml").read_text())
    for fid, spec in cfg["festivals"].items():
        for year, entries in spec["years"].items():
            for key, e in entries.items():
                if e["verified"]:
                    assert e["date"] is not None and e["source"] in cfg["sources"], (fid, year, key)
                else:
                    assert e["date"] is None, (fid, year, key)  # never a guessed date


# --------------------------------------------------------------------------- conditions
@pytest.mark.parametrize(
    "text",
    [
        "send 10000 to Ama only if rent is covered",
        "rent pugyo bhane matra Ama lai 10 hajar pathau",
        "bhada pugyo bhane matra pathau",
        "send 5000 to Dai after paying rent",
        "भाडा पुग्यो भने मात्र पठाउ",
    ],
)
def test_conditions(text: str) -> None:
    c = normalise(text, TODAY).conditions
    assert c and c[0].kind == "balance_after_reserve" and c[0].reserve == "rent" and c[0].op == ">="


def test_no_condition_without_marker_and_cover_word() -> None:
    assert normalise("pay my rent", TODAY).conditions == ()
    assert normalise("send 500 to Ama", TODAY).conditions == ()


@pytest.mark.parametrize(
    ("text", "freq"),
    [("every month 100 QAR to Suresh", "monthly"), ("har mahina pathau", "monthly"), ("monthly", "monthly"),
     ("हरेक महिना", "monthly"), ("har hapta", "weekly"), ("every week", "weekly")],
)  # fmt: skip
def test_recurrence(text: str, freq: str) -> None:
    assert normalise(text, TODAY).recurrences[0].freq == freq


# --------------------------------------------------------------------------- language style
@pytest.mark.parametrize(
    ("text", "tag"),
    [
        ("send 500 AUD to my mother", LanguageTag.EN),
        ("what is my balance", LanguageTag.EN),
        ("आमालाई २० हजार पठाउ", LanguageTag.NE),
        ("मेरो ब्यालेन्स कति छ", LanguageTag.NE),
        ("Ama lai 20 hajar pathaideu", LanguageTag.NE_LATN),
        ("mero balance kati chha", LanguageTag.NE_LATN),
        ("rent pugyo bhane matra pathau", LanguageTag.NE_LATN),
        ("send 20 hajar to Ama", LanguageTag.MIXED),
        ("Ama lai please 5k send gardeu", LanguageTag.MIXED),
        ("आमालाई send 20k", LanguageTag.MIXED),
    ],
)
def test_language_style(text: str, tag: LanguageTag) -> None:
    assert normalise(text, TODAY).language is tag


def test_empty_and_symbol_only_text() -> None:
    for t in ("", "   ", "!!!", "😀"):
        n = normalise(t, TODAY)
        assert n.amounts == () and n.currencies == ()


def test_no_two_different_numbers_share_a_folded_form() -> None:
    """Spelling folding must never merge two different number words (for example teen and ten)."""
    from corridor.nlu.lang_pack import DATA_DIR, _load

    seen: dict[str, int] = {}
    for code in PACKS.enabled:
        n = _load(DATA_DIR / "lang" / code / "numbers.yaml")
        for table in ("words", "multipliers"):
            for value, forms in (n.get(table) or {}).items():
                for f in [*(forms.get("deva") or []), *(forms.get("latn") or [])]:
                    key = PACKS.fold(f)
                    assert seen.setdefault(key, int(value)) == int(value), (f, key)
