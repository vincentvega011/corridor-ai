"""Deterministic normaliser: candidate values with evidence spans from the user's own text.

Pure Python, no LLM, no network. Everything it returns is a typed candidate that carries the
character span (``start``, ``end`` offsets into the ORIGINAL text) it came from. That evidence is
what later lets the parser mark a slot ``USER_TYPED`` (INV-4): a value that has no evidence span
is never trusted as something the user said.

What it understands (all vocabulary comes from ``data/lang`` packs, see ``lang_pack.py``):
- script and language style (English, Devanagari Nepali, romanised Nepali, mixed);
- amounts: ASCII and Devanagari digits, ``20k``, ``1.5 lakh``, ``1,00,000``, number words in
  English, Devanagari and romanised Nepali (``bis hajar``, ``बीस हजार``, ``ek lakh pachas hajar``);
- currency words (a bare ``dollar`` is AMBIGUOUS and never resolved), unsupported currencies;
- kinship and payee references (``ama``, ``दाइ``, ``university``) and capitalised names;
- dates: today, tomorrow, weekdays, periods, explicit dates, and Nepali festivals from
  ``data/calendar/festivals.yaml`` (only verified dates; otherwise "unknown");
- conditions such as "only if rent is covered" and recurrences such as "har mahina".

Conventions: amounts are exact ``Decimal`` major units internally and integer minor units on
output (never floats). Slashed numeric dates are read day first (``31/10/2026``), flagged
ambiguous when both parts are 12 or less.
"""

import calendar
import datetime as dt
import re
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from corridor.contracts.intent import LanguageTag
from corridor.contracts.money import CurrencyCode, minor_unit_exponent
from corridor.nlu.lang_pack import LangPacks, festival_date, is_devanagari, load_packs

MAX_UNITS = Decimal(10) ** 8 - 1  # larger amounts (9+ digits) are flagged out of range
_DEVA_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_TOKEN = re.compile(r"[ऀ-ॣ०-ॿ\w]+(?:[.,][ऀ-ॣ०-ॿ\w]+)*|[$₹€£¥%]")
_PIECE = re.compile(r"[0-9]+(?:[.,][0-9]+)*|[^0-9]+")
MAX_TEXT_CHARS = 2000  # longer input is refused (quadratic scans, and no chat message is this long)
MAX_DECIMAL_PLACES = 6


class TextTooLong(ValueError):
    """The message exceeds MAX_TEXT_CHARS; callers must fail closed."""


_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_SLASH = re.compile(r"(?<!\d)(\d{1,2})[/.](\d{1,2})[/.](\d{4})(?!\d)")
_ORDINAL = {"st", "nd", "rd", "th"}


class _M(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Span(_M):
    """Offsets into the original user text and the exact text they cover."""

    start: int
    end: int
    text: str


class Script(StrEnum):
    """Writing system of the message."""

    LATIN = "latin"
    DEVANAGARI = "devanagari"
    MIXED = "mixed"
    NONE = "none"


class CurrencyStatus(StrEnum):
    """Outcome of a currency word."""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"


class CurrencyCandidate(_M):
    """A currency mention. ``AMBIGUOUS`` lists the options and is never resolved to one."""

    span: Span
    status: CurrencyStatus
    code: CurrencyCode | None = None
    options: tuple[str, ...] = ()
    unsupported_code: str | None = None


class AmountCandidate(_M):
    """A number with its evidence. ``amount_minor`` uses the currency's minor unit exponent when
    the currency is known, otherwise 2 (``exponent_assumed``). None if not an integral minor unit
    or out of range."""

    span: Span
    units: str  # exact decimal string of major units
    amount_minor: int | None
    exponent_assumed: bool
    in_range: bool
    currency: CurrencyCandidate | None = None


class RelationCandidate(_M):
    """A kinship or role reference such as ``ama`` (mother) or ``university``."""

    span: Span
    relation: str
    term: str


class NameCandidate(_M):
    """A probable personal name (capitalised word or a word before a case marker)."""

    span: Span
    name: str


class DateKind(StrEnum):
    """Kind of date expression."""

    RELATIVE_DAY = "relative_day"
    WEEKDAY = "weekday"
    PERIOD = "period"
    EXPLICIT = "explicit"


class DateCandidate(_M):
    """A date expression resolved against ``today``. ``date`` is None if it cannot be resolved."""

    span: Span
    kind: DateKind
    date: date | None
    detail: str = ""
    ambiguous: bool = False
    is_past: bool = False
    rolled_year: bool = False  # no year was written and the next occurrence is next year


class FestivalCandidate(_M):
    """A festival deadline. ``before_festival`` is the day before the festival period starts,
    ``for_festival`` is the main day. ``date`` is None when no verified date exists."""

    span: Span
    festival_id: str
    deadline_kind: str  # before_festival | for_festival
    date: date | None
    year: int | None
    label: str | None
    verified: bool
    note: str | None = None
    source_grade: str | None = None  # official_calendar_site or computed_panchang
    conflicting_dates: tuple[dt.date, ...] = ()  # dates other sources gave (YAML `conflict`)


class ConditionCandidate(_M):
    """A structured condition, for example balance_after_reserve(rent) >= amount."""

    span: Span
    kind: str
    reserve: str
    op: str
    target: str


class RecurrenceCandidate(_M):
    """A recurrence such as monthly."""

    span: Span
    freq: str


class Normalised(_M):
    """Everything the normaliser found in one message."""

    original: str
    script: Script
    language: LanguageTag
    amounts: tuple[AmountCandidate, ...] = ()
    currencies: tuple[CurrencyCandidate, ...] = ()
    relations: tuple[RelationCandidate, ...] = ()
    names: tuple[NameCandidate, ...] = ()
    dates: tuple[DateCandidate, ...] = ()
    festivals: tuple[FestivalCandidate, ...] = ()
    conditions: tuple[ConditionCandidate, ...] = ()
    recurrences: tuple[RecurrenceCandidate, ...] = ()
    today: date | None = None


class _Tok:
    __slots__ = ("end", "folded", "is_num", "start", "text", "value")

    def __init__(self, start: int, end: int, text: str, folded: str, value: Decimal | None) -> None:
        self.start, self.end, self.text, self.folded = start, end, text, folded
        self.is_num = value is not None
        self.value = value


def _lower_same_length(text: str) -> str:
    return "".join(c.lower() if len(c.lower()) == 1 else c for c in text)


def _tokenise(text: str, packs: LangPacks) -> list[_Tok]:
    digits = text.translate(_DEVA_DIGITS)
    low = _lower_same_length(digits)
    toks: list[_Tok] = []
    for m in _TOKEN.finditer(low):
        base = m.start()
        for piece in _PIECE.finditer(m.group()):
            s, e = base + piece.start(), base + piece.end()
            raw = low[s:e]
            number = _parse_number(raw) if raw[0] in "0123456789" else None
            if number is not None:
                toks.append(_Tok(s, e, raw, raw, number))
            else:
                stripped = raw.strip(".,")
                if not stripped:
                    continue
                off = raw.index(stripped[0])
                s2 = s + off
                toks.append(_Tok(s2, s2 + len(stripped), stripped, packs.fold(stripped), None))
    return _split_glued(toks, packs)


def _split_glued(toks: list[_Tok], packs: LangPacks) -> list[_Tok]:
    """Split a postposition written glued to a time word (हिजोसम्म, दशैंअघि, dashainaghi) into two tokens.

    Data driven (``glued_postpositions`` in time.yaml). A token is split only when what is left is a
    known time word or festival name word, and never when the whole token is itself one, so a festival
    or a name is never cut at a look-alike ending."""
    out: list[_Tok] = []
    for t in toks:
        if t.is_num or t.folded in packs.time_stems or not packs.glued_postpositions:
            out.append(t)
            continue
        for suf in packs.glued_postpositions:
            if t.text.endswith(suf) and len(t.text) - len(suf) >= 2:
                stem = t.text[: -len(suf)]
                f = packs.fold(stem)
                if any(x in packs.time_stems for x in packs.strip_case(f)):
                    cut = t.start + len(stem)
                    out.append(_Tok(t.start, cut, stem, f, None))
                    out.append(_Tok(cut, t.end, suf, packs.fold(suf), None))
                    break
        else:
            out.append(t)
    return out


def _parse_number(raw: str) -> Decimal | None:
    """A plain number such as 20, 1,00,000 or 12.50. Anything else (1.2.3, 192.168.1.1, more than
    MAX_DECIMAL_PLACES decimals) is not a number and the token is treated as a word."""
    cleaned = raw.replace(",", "")
    if cleaned.count(".") > 1 or (
        cleaned.count(".") == 1 and len(cleaned.split(".")[1]) > MAX_DECIMAL_PLACES
    ):
        return None
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", cleaned) is None:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _span(text: str, a: int, b: int) -> Span:
    return Span(start=a, end=b, text=text[a:b])


def _lookup(table: dict[str, Any], tok: _Tok, packs: LangPacks) -> Any | None:
    for form in packs.strip_case(tok.folded):
        if form in table:
            return table[form]
    return None


def _match_seq(toks: Sequence[_Tok], i: int, seq: tuple[str, ...]) -> bool:
    if i + len(seq) > len(toks):
        return False
    return all(toks[i + k].folded == w for k, w in enumerate(seq))


def _script_of(text: str) -> Script:
    deva = sum(1 for c in text if "ऀ" <= c <= "ॿ")
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    if deva and latin:
        return Script.MIXED
    if deva:
        return Script.DEVANAGARI
    return Script.LATIN if latin else Script.NONE


def _language(text: str, toks: list[_Tok], packs: LangPacks, script: Script) -> LanguageTag:
    ne = en = 0
    for t in toks:
        if t.is_num or is_devanagari(t.text):
            continue
        if t.folded in packs.loans or t.folded in packs.currency_words:
            continue
        if t.folded in packs.romanised_ne or t.folded in packs.relations_ne_latin():
            ne += 1
        elif t.folded in packs.english_words:
            en += 1
    if script is Script.DEVANAGARI:
        return LanguageTag.NE
    if script is Script.MIXED:
        return LanguageTag.MIXED
    if script is Script.NONE:
        return LanguageTag.EN
    if ne == 0:
        return LanguageTag.EN
    if en == 0:
        return LanguageTag.NE_LATN
    return LanguageTag.MIXED


# ---------------------------------------------------------------------------------- numbers
def _combine(items: list[tuple[str, Decimal]]) -> Decimal:
    """Indian style number combination: (kind, value) with kind in num, mult."""
    total, current = Decimal(0), Decimal(0)
    with localcontext() as ctx:
        ctx.prec = 80  # never round a typed value silently
        for kind, v in items:
            if kind == "num":
                current += v
            elif v == 100:
                current = (current if current else Decimal(1)) * 100
            else:
                total += (current if current else Decimal(1)) * v
                current = Decimal(0)
        return total + current


def _number_groups(
    toks: list[_Tok], packs: LangPacks, blocked: set[int]
) -> list[tuple[int, int, Decimal, bool]]:
    """Find number groups: (first_idx, last_idx, value, has_digit). ``blocked`` token indexes are
    inside dates and excluded."""
    kinds: list[tuple[str, Decimal] | None] = []
    for i, t in enumerate(toks):
        if i in blocked:
            kinds.append(None)
        elif t.is_num:
            kinds.append(("digit", t.value or Decimal(0)))
        elif (
            t.folded in packs.suffixes
            and i > 0
            and toks[i - 1].is_num
            and toks[i - 1].end == t.start
        ):
            kinds.append(("suffix", Decimal(packs.suffixes[t.folded])))
        elif t.folded in packs.multipliers:
            kinds.append(("mult", Decimal(packs.multipliers[t.folded])))
        elif t.folded in packs.number_words:
            kinds.append(("word", Decimal(packs.number_words[t.folded])))
        else:
            kinds.append(None)
    groups: list[tuple[int, int, Decimal, bool]] = []
    i = 0
    while i < len(toks):
        k = kinds[i]
        if k is None or k[0] == "suffix":
            i += 1
            continue
        items: list[tuple[str, Decimal]] = []
        j, prev = i, ""
        last = i
        while j < len(toks):
            kj = kinds[j]
            if kj is None:
                if (
                    toks[j].folded in packs.connectors
                    and prev in ("word", "mult")
                    and j + 1 < len(toks)
                    and (nk := kinds[j + 1]) is not None
                    and nk[0] in ("word", "digit")
                ):
                    j += 1
                    continue
                break
            kind, v = kj
            if kind == "suffix":
                items.append(("mult", v))
                prev, last = "mult", j
                j += 1
                continue
            if kind == "digit":
                if prev in ("digit", "word"):
                    break
                items.append(("num", v))
            elif kind == "word":
                if prev == "digit":
                    break
                items.append(("num", v))
            else:  # mult
                items.append(("mult", v))
            prev, last = kind, j
            j += 1
        has_digit = any(kinds[x] and kinds[x][0] == "digit" for x in range(i, last + 1))  # type: ignore[index]
        groups.append((i, last, _combine(items), has_digit))
        i = last + 1
    return groups


def _next_is_unit(toks: list[_Tok], idx: int, packs: LangPacks) -> bool:
    if idx + 1 >= len(toks):
        return False
    nxt = toks[idx + 1]
    return nxt.folded in packs.duration_units or nxt.folded == "%" or nxt.folded in _ORDINAL


# ---------------------------------------------------------------------------------- dates
def _resolve_year(today: date, month: int, day: int) -> date | None:
    for year in (today.year, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        if d >= today:
            return d
    return None


def _end_of_month(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def _find_dates(
    text: str, toks: list[_Tok], packs: LangPacks, today: date
) -> tuple[list[DateCandidate], set[int]]:
    out: list[DateCandidate] = []
    used: set[int] = set()
    digits = text.translate(_DEVA_DIGITS)

    def mark(a: int, b: int) -> None:
        used.update(i for i, t in enumerate(toks) if t.start < b and t.end > a)

    for m in _ISO.finditer(digits):
        try:
            d: date | None = date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            d = None
        out.append(
            DateCandidate(
                span=_span(text, m.start(), m.end()),
                kind=DateKind.EXPLICIT,
                date=d,
                detail="iso",
                is_past=bool(d and d < today),
            )
        )
        mark(m.start(), m.end())
    for m in _SLASH.finditer(digits):
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        try:
            d = date(y, b, a)  # day first
        except ValueError:
            d = None
        out.append(
            DateCandidate(
                span=_span(text, m.start(), m.end()),
                kind=DateKind.EXPLICIT,
                date=d,
                detail="dmy",
                ambiguous=a <= 12 and b <= 12 and a != b,
                is_past=bool(d and d < today),
            )
        )
        mark(m.start(), m.end())
    i = 0
    while i < len(toks):
        t = toks[i]
        if i in used:
            i += 1
            continue
        # relative periods (multi token) first
        matched = False
        for seq, period in packs.relative_periods:
            if _match_seq(toks, i, seq):
                if period == "this_week":
                    d2 = today + timedelta(
                        days=(4 - today.weekday()) % 7
                    )  # Friday as end of the working week
                elif period == "next_week":
                    d2 = today + timedelta(days=(4 - today.weekday()) % 7 + 7)
                elif period == "this_month":
                    d2 = _end_of_month(today)
                else:
                    nxt = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
                    d2 = _end_of_month(nxt)
                end = toks[i + len(seq) - 1].end
                out.append(
                    DateCandidate(
                        span=_span(text, t.start, end), kind=DateKind.PERIOD, date=d2, detail=period
                    )
                )
                used.update(range(i, i + len(seq)))
                i += len(seq)
                matched = True
                break
        if matched:
            continue
        off = _lookup(packs.relative_days, t, packs)
        if off is not None and len(t.text) != 1:
            d3 = today + timedelta(days=off)
            out.append(
                DateCandidate(
                    span=_span(text, t.start, t.end),
                    kind=DateKind.RELATIVE_DAY,
                    date=d3,
                    detail=t.text,
                    is_past=off < 0,
                )
            )
            used.add(i)
            i += 1
            continue
        wd = _lookup(packs.weekdays, t, packs)
        if wd is not None:
            nxt_marker = i > 0 and toks[i - 1].folded in packs.weekday_next
            ahead = (wd - today.weekday()) % 7
            if ahead == 0 or nxt_marker:
                ahead += 7 if ahead == 0 else 0
            if nxt_marker and ahead < 7 and ahead != 0:
                ahead += 7
            start_i = i - 1 if nxt_marker else i
            a = toks[start_i].start
            out.append(
                DateCandidate(
                    span=_span(text, a, t.end),
                    kind=DateKind.WEEKDAY,
                    date=today + timedelta(days=ahead),
                    detail=t.text,
                )
            )
            used.update(range(start_i, i + 1))
            i += 1
            continue
        mon = packs.months.get(t.folded)
        if mon is not None:
            day = None
            lo = i
            hi = i
            if i > 0 and toks[i - 1].is_num and 1 <= (toks[i - 1].value or 0) <= 31:
                day, lo = int(toks[i - 1].value or 0), i - 1
            elif i + 1 < len(toks) and toks[i + 1].is_num and 1 <= (toks[i + 1].value or 0) <= 31:
                day, hi = int(toks[i + 1].value or 0), i + 1
            if day is not None:
                year = None
                if (
                    hi + 1 < len(toks)
                    and toks[hi + 1].is_num
                    and 1900 < (toks[hi + 1].value or 0) < 2200
                ):
                    year, hi = int(toks[hi + 1].value or 0), hi + 1
                if year is not None:
                    try:
                        dd: date | None = date(year, mon, day)
                    except ValueError:
                        dd = None
                else:
                    dd = _resolve_year(today, mon, day)
                out.append(
                    DateCandidate(
                        span=_span(text, toks[lo].start, toks[hi].end),
                        kind=DateKind.EXPLICIT,
                        date=dd,
                        detail="day_month",
                        is_past=bool(dd and dd < today),
                        rolled_year=bool(year is None and dd and dd.year > today.year),
                    )
                )
                used.update(range(lo, hi + 1))
                i = hi + 1
                continue
        i += 1
    return out, used


def _find_festivals(
    text: str, toks: list[_Tok], packs: LangPacks, today: date
) -> tuple[list[FestivalCandidate], set[int]]:
    out: list[FestivalCandidate] = []
    used: set[int] = set()
    # longest name first, so "dashain suru huna" (Ghatasthapana) wins over plain "dashain"
    pairs = sorted(((f, n) for f in packs.festivals for n in f.names), key=lambda fn: -len(fn[1]))
    for fest, name in pairs:
        for i in range(len(toks)):
            # allow a Devanagari case suffix on the last token (दशैंभन्दा)
            if i + len(name) > len(toks):
                continue
            ok = all(
                (toks[i + k].folded == w)
                or (k == len(name) - 1 and w in packs.strip_case(toks[i + k].folded))
                for k, w in enumerate(name)
            )
            if not ok or any((i + k) in used for k in range(len(name))):
                continue
            last = i + len(name) - 1
            after_seq = next(
                (q for q in packs.after_markers if _match_seq(toks, last + 1, q)), None
            )
            before_after = i > 0 and any(
                len(q) == 1 and toks[i - 1].folded == q[0] for q in packs.after_markers
            )
            if after_seq is not None or before_after:
                # "after Dashain" / "dashain pachhi": not a deadline. Claim the tokens, build nothing.
                used.update(range(i, last + 1 + (len(after_seq) if after_seq else 0)))
                continue
            glued_before = toks[last].folded != name[-1]  # "दशैंभन्दा" style
            kind = "for_festival"
            end_idx = last
            # marker after the festival name
            for seq in sorted(packs.before_markers, key=len, reverse=True):
                if _match_seq(toks, last + 1, seq):
                    kind, end_idx = "before_festival", last + len(seq)
                    break
            if kind != "before_festival" and glued_before:
                kind = "before_festival"
                if last + 1 < len(toks) and toks[last + 1].folded in {
                    packs.fold("अगाडि"),
                    packs.fold("पहिले"),
                    packs.fold("अघि"),
                }:
                    end_idx = last + 1
            if kind != "before_festival" and i > 0:  # marker before: "before dashain", "by dashain"
                for seq in packs.before_markers:
                    if (
                        len(seq) == 1
                        and toks[i - 1].folded == seq[0]
                        and seq[0] in {"before", "by", "until", "till"}
                    ):
                        kind = "before_festival"
                        break
            # "before X" and "for X" both mean: on or before the festival's main day (the Tika day for
            # Dashain). "before Dashain starts" is the festival dashain_start (Ghatasthapana).
            d, year, entry = festival_date(fest, today, "main_day")
            a = (
                toks[i - 1].start
                if kind == "before_festival"
                and i > 0
                and toks[i - 1].folded in {"before", "by", "until", "till"}
                else toks[i].start
            )
            b = toks[end_idx].end
            note = entry.get("note") if entry else None
            out.append(
                FestivalCandidate(
                    span=_span(text, a, b),
                    festival_id=fest.festival_id,
                    deadline_kind=kind,
                    date=d,
                    year=year,
                    label=entry.get("label") if entry else None,
                    verified=d is not None,
                    note=note,
                    source_grade=entry.get("grade") if entry else None,
                    conflicting_dates=tuple(entry.get("conflict") or ()) if entry else (),
                )
            )
            used.update(range(i, end_idx + 1))
    return out, used


# ---------------------------------------------------------------------------------- the rest
def _find_currencies(
    text: str, toks: list[_Tok], packs: LangPacks, used: set[int]
) -> list[tuple[int, int, CurrencyCandidate]]:
    out: list[tuple[int, int, CurrencyCandidate]] = []
    i = 0
    while i < len(toks):
        if i in used:
            i += 1
            continue
        phrase = next(
            ((seq, c) for seq, c in packs.currency_phrases if _match_seq(toks, i, seq)), None
        )
        if phrase:
            seq, code = phrase
            j = i + len(seq) - 1
            out.append(
                (
                    i,
                    j,
                    CurrencyCandidate(
                        span=_span(text, toks[i].start, toks[j].end),
                        status=CurrencyStatus.RESOLVED,
                        code=CurrencyCode(code),
                    ),
                )
            )
            i = j + 1
            continue
        t = toks[i]
        found = _lookup(packs.currency_words, t, packs)
        if found is not None and not t.is_num:
            code = str(found)
            out.append(
                (
                    i,
                    i,
                    CurrencyCandidate(
                        span=_span(text, t.start, t.end),
                        status=CurrencyStatus.RESOLVED,
                        code=CurrencyCode(code),
                    ),
                )
            )
        else:
            amb = _lookup(packs.currency_ambiguous, t, packs)
            if amb is not None:
                out.append(
                    (
                        i,
                        i,
                        CurrencyCandidate(
                            span=_span(text, t.start, t.end),
                            status=CurrencyStatus.AMBIGUOUS,
                            options=tuple(amb),
                        ),
                    )
                )
            else:
                uns = _lookup(packs.currency_unsupported, t, packs)
                if uns is not None:
                    out.append(
                        (
                            i,
                            i,
                            CurrencyCandidate(
                                span=_span(text, t.start, t.end),
                                status=CurrencyStatus.UNSUPPORTED,
                                unsupported_code=uns,
                            ),
                        )
                    )
        i += 1
    return out


def _find_relations(
    text: str, toks: list[_Tok], packs: LangPacks, skip: set[int]
) -> list[RelationCandidate]:
    out = []
    for i, t in enumerate(toks):
        if i in skip or t.is_num:
            continue
        rel = _lookup(packs.relations, t, packs)
        if rel is not None:
            out.append(
                RelationCandidate(
                    span=_span(text, t.start, t.end), relation=rel, term=text[t.start : t.end]
                )
            )
    return out


def _find_names(
    text: str, toks: list[_Tok], packs: LangPacks, skip: set[int]
) -> list[NameCandidate]:
    known = (
        packs.romanised_ne
        | packs.english_words
        | packs.loans
        | set(packs.months)
        | set(packs.weekdays)
        | packs.name_noise
    )
    out = []
    for i, t in enumerate(toks):
        if i in skip or t.is_num or t.folded in known or t.folded in packs.non_names:
            continue
        if _lookup(packs.relations, t, packs) is not None or t.folded in packs.currency_words:
            continue
        raw = text[t.start : t.end]
        if is_devanagari(raw):
            stem = next(
                (
                    s
                    for s in packs.strip_case(t.folded)[1:]
                    if len(s) >= 3 and s not in packs.non_names
                ),
                None,
            )
            if (
                stem
                and stem not in packs.relations
                and stem not in packs.number_words
                and stem not in packs.multipliers
                and stem not in packs.name_noise
            ):
                # "रमेशलाई" names रमेश: the case suffix is not part of the name (the span is trimmed to the stem)
                end = t.start + len(stem) if len(packs.fold(raw)) == len(raw) else t.end
                out.append(NameCandidate(span=_span(text, t.start, end), name=text[t.start : end]))
            continue
        glued = _glued_latin_name(raw, packs)
        if (
            glued is not None
        ):  # "Rameshlai", "Rameshko": a romanised case marker written without a space
            end = t.start + len(glued)
            out.append(NameCandidate(span=_span(text, t.start, end), name=text[t.start : end]))
            continue
        if raw[:1].isupper() and not raw.isupper() and len(raw) > 1:
            starts_sentence = i == 0
            nxt = toks[i + 1].folded if i + 1 < len(toks) else ""
            prev = toks[i - 1].folded if i > 0 else ""
            if (
                not starts_sentence
                or nxt in {"lai", "ko", "ka", "ki"}
                or nxt in packs.name_honorifics
                or nxt in packs.honorific_terms
                or prev in {"to", "for"}
                or (starts_sentence and nxt and nxt in packs.romanised_ne)
            ):
                out.append(NameCandidate(span=_span(text, t.start, t.end), name=raw))
        elif (
            raw.isalpha()
            and raw.islower()
            and len(raw) >= 3
            and i + 1 < len(toks)
            and toks[i + 1].folded == "lai"
        ):  # an unknown lower case name before "lai" ("bob lai 5 hajar pathau"): kept, the agent asks who it is
            out.append(NameCandidate(span=_span(text, t.start, t.end), name=raw))
    return _merge_adjacent_names(text, _decorate_names(text, toks, out, packs))


def _glued_latin_name(raw: str, packs: LangPacks) -> str | None:
    """The name in ``Rameshlai`` (a romanised case marker glued to a name), or None.

    Only markers of three or more letters count (``lai``, ``bata``, ``sanga``, ``tira``): ``-ki``,
    ``-ko``, ``-ka``, ``-ma`` and ``-le`` end real names ("Karki", "Sharma", "Rai") and ordinary words,
    and a single clipped letter would turn "Karki" into "Kar". Those short markers are still read
    when written apart ("Ramesh ko"). A lower case word accepts only ``lai``, ``bata`` and ``sanga``."""
    if not raw.isascii() or not raw.isalpha() or len(raw) < 5:
        return None
    folded = packs.fold(raw)
    if is_known_word(folded, packs):
        return None
    capital = raw[:1].isupper() and not raw.isupper()
    for suf in packs.case_suffixes_latn:
        if len(suf) < 3 or not folded.endswith(suf) or len(raw) - len(suf) < (3 if capital else 4):
            continue
        if not capital and suf not in {"lai", "bata", "sanga"}:
            continue
        stem = raw[: -len(suf)]
        if not is_known_word(packs.fold(stem), packs):
            return stem
    return None


def _decorate_names(
    text: str, toks: list[_Tok], names: list[NameCandidate], packs: LangPacks
) -> list[NameCandidate]:
    """Pull a title before a name ("Mr Chen") and an honorific after it ("Ramesh dai", "Kumar sir")
    into the name's span. The decoration is not part of the identity: ``resolve_payee`` ignores it."""
    by_start = {t.start: i for i, t in enumerate(toks)}
    by_end = {t.end: i for i, t in enumerate(toks)}
    out: list[NameCandidate] = []
    for n in names:
        start, end = n.span.start, n.span.end
        j, k = by_start.get(start), by_end.get(end)
        if (
            j is not None
            and j > 0
            and toks[j - 1].folded in packs.name_titles
            and text[toks[j - 1].end : start].strip() == ""
        ):
            start = toks[j - 1].start
        if k is not None and k + 1 < len(toks):
            nxt = toks[k + 1]
            if (nxt.folded in packs.name_honorifics or nxt.folded in packs.honorific_terms) and (
                text[end : nxt.start].strip() == ""
            ):
                end = nxt.end
        out.append(NameCandidate(span=_span(text, start, end), name=text[start:end]))
    return out


def _merge_adjacent_names(text: str, names: list[NameCandidate]) -> list[NameCandidate]:
    """Join names separated only by spaces ("Mr Chen", "Harbourview University") into one candidate."""
    merged: list[NameCandidate] = []
    for n in names:
        if (
            merged
            and text[merged[-1].span.end : n.span.start].strip() == ""
            and n.span.start - merged[-1].span.end <= 2
        ):
            a = merged[-1]
            merged[-1] = NameCandidate(
                span=_span(text, a.span.start, n.span.end), name=text[a.span.start : n.span.end]
            )
        else:
            merged.append(n)
    return merged


def is_known_word(folded: str, packs: LangPacks) -> bool:
    """True for any word the packs know (function words, loans, months, weekdays, numbers, currencies,
    kinship, case markers). Unknown words may be names."""
    return (
        folded in packs.romanised_ne
        or folded in packs.english_words
        or folded in packs.loans
        or folded in packs.months
        or folded in packs.weekdays
        or folded in packs.number_words
        or folded in packs.multipliers
        or folded in packs.currency_words
        or folded in packs.relations
        or folded in packs.non_names
    )


def _find_conditions(text: str, toks: list[_Tok], packs: LangPacks) -> list[ConditionCandidate]:
    marker_idx = [i for i, t in enumerate(toks) if t.folded in packs.condition_markers]
    reserve_idx = [
        (i, packs.reserve_words[t.folded])
        for i, t in enumerate(toks)
        if t.folded in packs.reserve_words
    ]
    cover_idx = [i for i, t in enumerate(toks) if t.folded in packs.cover_triggers]
    out: list[ConditionCandidate] = []
    for ri, reserve in reserve_idx:
        near_m = [i for i in marker_idx if abs(i - ri) <= 6]
        near_c = [i for i in cover_idx if abs(i - ri) <= 4]
        if near_m and near_c:
            idx = [ri, *near_m, *near_c]
            a, b = min(idx), max(idx)
            out.append(
                ConditionCandidate(
                    span=_span(text, toks[a].start, toks[b].end),
                    kind="balance_after_reserve",
                    reserve=reserve,
                    op=">=",
                    target="amount",
                )
            )
            break
    return out


def _find_recurrence(text: str, toks: list[_Tok], packs: LangPacks) -> list[RecurrenceCandidate]:
    out = []
    ordered = sorted(packs.recurrence, key=lambda x: -len(x[0]))
    for i in range(len(toks)):
        for seq, freq in ordered:
            if _match_seq(toks, i, seq):
                out.append(
                    RecurrenceCandidate(
                        span=_span(text, toks[i].start, toks[i + len(seq) - 1].end), freq=freq
                    )
                )
                break
    return out


def _amount_minor(units: Decimal, currency: CurrencyCode | None) -> tuple[int | None, bool]:
    exp = minor_unit_exponent(currency) if currency else 2
    minor = units * (Decimal(10) ** exp)
    if minor != minor.to_integral_value():
        return None, currency is None
    return int(minor), currency is None


def normalise(text: str, today: date, packs: LangPacks | None = None) -> Normalised:
    """Normalise ``text`` into typed candidates with evidence spans. ``today`` anchors relative dates."""
    if len(text) > MAX_TEXT_CHARS:
        raise TextTooLong(f"message longer than {MAX_TEXT_CHARS} characters")
    packs = packs or load_packs()
    toks = _tokenise(text, packs)
    script = _script_of(text)
    language = _language(text, toks, packs, script)

    dates, date_tokens = _find_dates(text, toks, packs, today)
    festivals, fest_tokens = _find_festivals(text, toks, packs, today)
    blocked = date_tokens | fest_tokens
    currencies_raw = _find_currencies(text, toks, packs, set())
    cur_tokens = {k for a, b, _ in currencies_raw for k in range(a, b + 1)}

    amounts: list[AmountCandidate] = []
    amount_tokens: set[int] = set()
    for first, last, value, has_digit in _number_groups(toks, packs, blocked):
        if _next_is_unit(toks, last, packs) and not any(
            a <= last + 1 <= b for a, b, _ in currencies_raw
        ):
            continue
        words_only = not has_digit
        has_mult = any(
            toks[k].folded in packs.multipliers or toks[k].folded in packs.suffixes
            for k in range(first, last + 1)
        )
        attached = next(
            (c for a, b, c in currencies_raw if b == first - 1 or (a == last + 1)), None
        )
        if words_only and not has_mult and attached is None:
            single_ambiguous = any(
                toks[k].folded in packs.ambiguous_alone for k in range(first, last + 1)
            )
            if single_ambiguous or value < 11:
                continue
        if value == 0 and words_only:
            continue
        code = attached.code if attached and attached.status is CurrencyStatus.RESOLVED else None
        amount_tokens.update(range(first, last + 1))
        in_range = value <= MAX_UNITS
        minor, assumed = _amount_minor(value, code) if in_range else (None, code is None)
        amounts.append(
            AmountCandidate(
                span=_span(text, toks[first].start, toks[last].end),
                units=format(value.normalize(), "f") if value != 0 else "0",
                amount_minor=minor if in_range else None,
                exponent_assumed=assumed,
                in_range=in_range,
                currency=attached,
            )
        )

    used_for_names = blocked | cur_tokens | amount_tokens
    relations = _find_relations(text, toks, packs, blocked)
    rel_tokens = {i for i, t in enumerate(toks) if any(r.span.start == t.start for r in relations)}
    names = _find_names(text, toks, packs, used_for_names | rel_tokens)
    # "Ramesh dai" is a named person addressed respectfully, not a request for "an elder brother"
    relations = [
        r
        for r in relations
        if not any(n.span.start <= r.span.start and r.span.end <= n.span.end for n in names)
    ]
    return Normalised(
        original=text,
        script=script,
        language=language,
        amounts=tuple(amounts),
        currencies=tuple(c for _, _, c in currencies_raw),
        relations=tuple(relations),
        names=tuple(names),
        dates=tuple(dates),
        festivals=tuple(festivals),
        conditions=tuple(_find_conditions(text, toks, packs)),
        recurrences=tuple(_find_recurrence(text, toks, packs)),
        today=today,
    )
