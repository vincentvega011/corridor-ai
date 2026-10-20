"""Find every number in a reply and say what kind it is (docs/spec/modules.md 5.14, INV-9).

Handles Devanagari digits, Western grouping (1,000,000) and Nepali grouping (1,00,000), the words
lakh, crore, hajar and thousand (also in Devanagari), currency codes and symbols (Rs, रू, $),
percentages, rates and dates. Identifiers such as ``inv_priya_2026s2``, ``****4471`` and ``S2026-0417``
are removed first so their digits are never mistaken for amounts.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from enum import StrEnum

from corridor.contracts.money import CurrencyCode, minor_unit_exponent

_DEVA = str.maketrans("०१२३४५६७८९", "0123456789")
MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}
_NUM = (
    r"(?:\d{1,3}(?:[,\u066c]\d{2,3})+(?:\.\d+)?|\d{1,3}(?: \d{3})+(?!\d)(?:\.\d+)?|\d+(?:\.\d+)?)"
)
_WORD = r"(?:lakhs?|lacs?|crores?|cr\b|hajar|hazar|thousand|million|billion|hundred|लाख|करोड|हजार|(?-i:k(?![a-z])))"
_CCY = r"(?:NPR|AUD|QAR|MYR|CNY|USD|INR|NRs\.?|Rs\.?|रू\.?|रु\.?|rupees?|\$|₹)"
_CCY_AFTER = r"(?:NPR|AUD|QAR|MYR|CNY|USD|INR|NRs\.?|Rs\.?|रू|रु|rupees|rupee|रुपैयाँ|रुपये|\$|₹)"
_MULT = {
    "lakh": 100_000, "lakhs": 100_000, "लाख": 100_000, "crore": 10_000_000, "crores": 10_000_000,
    "करोड": 10_000_000, "hajar": 1000, "hazar": 1000, "thousand": 1000, "हजार": 1000, "k": 1000,
    "lac": 100_000, "lacs": 100_000, "cr": 10_000_000, "million": 1_000_000, "billion": 1_000_000_000, "hundred": 100,
}  # fmt: skip
_UNITS = r"(?:h|hr|hrs|hours?|days?|months?|weeks?|transfers?|payees?|invoices?|attempts?)"

_IDENT = re.compile(
    r"\*+\d+"  # masked accounts
    r"|\b(?!(?:NPR|AUD|QAR|MYR|CNY|USD|NRs|Rs)_\d)[A-Za-z][A-Za-z0-9]*(?:_(?:\d{1,3}|[A-Za-z][A-Za-z0-9]*))+\b"  # snake_case ids such as inv_priya_2026s2 or d_ab12
    r"|\b(?!(?:NPR|AUD|QAR|MYR|CNY|USD|NRs|Rs)[\d.\-_])[A-Za-z]{1,6}-?\d[\w-]*\b"  # S2026-0417, INV-2026-S2-00417
    r"|\b(?:ref|reference|id|no|number|txn|tx|invoice)[\s.:#-]*\d{4,}\b(?!\s*(?:NPR|AUD|QAR|MYR|CNY|USD|INR|NRs|Rs|rupees?|रू|रु|\$|₹))",  # references and ids
    re.IGNORECASE,
)
_GLUED = re.compile(r"(?<![\w-])[A-Za-z]{2,}_?(?P<num>\d{5,})\b")
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DATE_DMY = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?[\s/-]+([A-Za-z]{3,9})\.?,?[\s/-]+(\d{4})\b"
)
_DATE_NOYEAR = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b|\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)
_DATE_NUM = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b|\b(\d{4})/(\d{1,2})/(\d{1,2})\b")
_DATE_MDY = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b")
_PCT = re.compile(rf"({_NUM})\s*(?:%|percent\b)", re.IGNORECASE)
_MONEY_BEFORE = re.compile(
    rf"(?<![A-Za-z_\d])(?P<ccy>{_CCY})[\s\-_.:]*(?P<num>{_NUM})(?:\s*(?P<word>{_WORD}))?",
    re.IGNORECASE,
)
_MONEY_AFTER = re.compile(
    rf"(?<![\w.])(?P<num>{_NUM})(?:\s*(?P<word>{_WORD}))?\s*(?P<ccy>{_CCY_AFTER})(?![A-Za-z_])",
    re.IGNORECASE,
)
_BARE = re.compile(rf"(?<![\w.])(?P<num>{_NUM})(?:\s*(?P<word>{_WORD}))?(?![\w:])", re.IGNORECASE)
_RATE_RHS = re.compile(
    rf"(?:\b1\s*(?:{_CCY_AFTER}|{_CCY})\s*)?=\s*(?P<num>{_NUM})\s*(?:{_CCY_AFTER}|{_CCY})?",
    re.IGNORECASE,
)
_UNIT_AFTER = re.compile(rf"\s*{_UNITS}\b", re.IGNORECASE)


class Kind(StrEnum):
    MONEY = "money"
    PERCENT = "percent"
    RATE = "rate"
    DATE = "date"
    COUNT = "count"
    NUMBER = "number"


@dataclass(frozen=True)
class Found:
    """One number found in a reply."""

    kind: Kind
    raw: str
    value: Decimal | None = None
    currency: CurrencyCode | None = None
    minor: int | None = None
    on: date | None = None
    ambiguous_currency: bool = False


def normalise_text(text: str) -> str:
    """Every Unicode decimal digit (Devanagari, Arabic-Indic, fullwidth ...) to ASCII, NFKC, no zero
    width characters, and ``5_00_000`` grouping written with underscores as ``5,00,000``."""
    out = unicodedata.normalize("NFKC", text)
    out = "".join(str(unicodedata.digit(c)) if unicodedata.category(c) == "Nd" else c for c in out)
    out = re.sub(r"(?<=\d)_(?=\d)", ",", out)
    return "".join(c for c in out if c not in "\u200b\u200c\u200d")


def _decimal(num: str, word: str | None) -> Decimal | None:
    try:
        value = Decimal(re.sub(r"[,\u066c ]", "", num))
    except InvalidOperation:
        return None
    if word:
        value *= _MULT[word.casefold()]
    return value


def _ccy(symbol: str) -> tuple[CurrencyCode | None, bool]:
    s = symbol.casefold().rstrip(".")
    if s in {"rs", "nrs", "रू", "रु", "rupees", "rupee", "रुपैयाँ", "रुपये"}:
        return CurrencyCode.NPR, False
    if s == "₹":
        return None, True  # the Indian rupee sign is not our rupee: never grounds against NPR
    if s == "$":
        return None, True  # a bare dollar sign never resolves to one currency
    try:
        return CurrencyCode(symbol.upper()), False
    except ValueError:
        return None, True  # a currency we do not support (INR): it never grounds against ours


def _blank(text: str, span: tuple[int, int]) -> str:
    return text[: span[0]] + " " * (span[1] - span[0]) + text[span[1] :]


def extract_numbers(reply: str) -> list[Found]:
    """Every number in ``reply`` with its kind. Identifiers are ignored."""
    text = normalise_text(reply)
    found: list[Found] = []
    # dates first: "15-Nov-2026" must not be taken for an identifier
    for rx, kind in (
        (_DATE_ISO, "iso"), (_DATE_DMY, "dmy"), (_DATE_MDY, "mdy"), (_DATE_NUM, "num"),
    ):  # fmt: skip
        for m in list(rx.finditer(text)):
            try:
                if kind == "iso":
                    d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                elif kind == "dmy":
                    d = date(int(m.group(3)), MONTHS[m.group(2)[:3].casefold()], int(m.group(1)))
                elif kind == "mdy":
                    d = date(int(m.group(3)), MONTHS[m.group(1)[:3].casefold()], int(m.group(2)))
                elif m.group(4):
                    d = date(int(m.group(4)), int(m.group(5)), int(m.group(6)))
                else:  # day first, as written in Nepal and Australia
                    d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except (ValueError, KeyError):
                continue
            found.append(Found(Kind.DATE, m.group(0), on=d))
            text = _blank(text, m.span())
    for m in list(_DATE_NOYEAR.finditer(text)):  # a date without a year cannot be grounded
        month_day = MONTHS[(m.group(2) or m.group(3))[:3].casefold()] * 100 + int(
            m.group(1) or m.group(4)
        )
        found.append(Found(Kind.DATE, m.group(0), on=None, value=Decimal(month_day)))
        text = _blank(text, m.span())
    for m in list(_GLUED.finditer(text)):  # digits glued to letters: a long number is not an id
        found.append(Found(Kind.NUMBER, m.group("num"), value=Decimal(m.group("num"))))
        text = _blank(text, m.span())
    text = _IDENT.sub(lambda m: " " * len(m.group(0)), text)
    for m in list(_PCT.finditer(text)):
        found.append(
            Found(
                Kind.PERCENT,
                m.group(0),
                value=_decimal(m.group("num") if "num" in m.groupdict() else m.group(1), None),
            )
        )
        text = _blank(text, m.span())
    for m in list(_RATE_RHS.finditer(text)):
        found.append(Found(Kind.RATE, m.group(0), value=_decimal(m.group("num"), None)))
        text = _blank(text, m.span())
    for rx in (_MONEY_BEFORE, _MONEY_AFTER):
        for m in list(rx.finditer(text)):
            value = _decimal(m.group("num"), m.group("word"))
            if value is None:
                continue
            if text[m.end() :].lstrip().startswith("="):
                text = _blank(text, m.span())  # "1 AUD = 90.12 NPR" names a unit, not an amount
                continue
            ccy, ambiguous = _ccy(m.group("ccy"))
            frac = len(m.group("num").split(".")[1]) if "." in m.group("num") else 0
            exp = minor_unit_exponent(ccy or CurrencyCode.USD)
            if frac > exp and not m.group("word"):
                found.append(Found(Kind.RATE, m.group(0), value=value, currency=ccy))
            else:
                minor = int((value * 10**exp).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
                found.append(
                    Found(
                        Kind.MONEY,
                        m.group(0),
                        value=value,
                        currency=ccy,
                        minor=minor,
                        ambiguous_currency=ambiguous,
                    )
                )
            text = _blank(text, m.span())
    for m in list(_BARE.finditer(text)):
        value = _decimal(m.group("num"), m.group("word"))
        if value is None:
            continue
        frac = len(m.group("num").split(".")[1]) if "." in m.group("num") else 0
        unit = _UNIT_AFTER.match(text, m.end())
        if unit and frac == 0:
            found.append(Found(Kind.COUNT, m.group(0), value=value))
        elif frac >= 1:  # any decimal that is not money, a percentage or a date is a rate claim
            found.append(Found(Kind.RATE, m.group(0), value=value))
        elif frac == 0 and (value >= 100 or "," in m.group("num")) and not (1900 <= value <= 2100):
            found.append(Found(Kind.NUMBER, m.group(0), value=value))
    found += _spelled_numbers(reply)
    return found


def _spelled_numbers(reply: str) -> list[Found]:
    """Numbers written as words ("twenty thousand", "bis hajar", "ek lakh", "बीस हजार"), found with the
    NLU normaliser's own number word parser so both stay in step. Fails closed: if the parser cannot
    run, a number that cannot be checked is reported (and the reply is blocked)."""
    from corridor.nlu.normalise import TextTooLong, normalise

    try:
        norm = normalise(reply, date(2026, 1, 1))
    except TextTooLong:
        return [Found(Kind.NUMBER, "<reply too long to check>")]
    except Exception:
        return [Found(Kind.NUMBER, "<number words could not be checked>")]
    out: list[Found] = []
    for cand in norm.amounts:
        if any(ch.isdigit() for ch in cand.span.text):
            continue  # digits are handled by the exact extraction above
        value = Decimal(cand.units)
        code = cand.currency.code if cand.currency is not None else None
        if code is not None:
            minor = int(
                (value * 10 ** minor_unit_exponent(code)).quantize(
                    Decimal(1), rounding=ROUND_HALF_EVEN
                )
            )
            out.append(Found(Kind.MONEY, cand.span.text, value=value, currency=code, minor=minor))
        elif value >= 100:
            out.append(Found(Kind.NUMBER, cand.span.text, value=value))
    return out
