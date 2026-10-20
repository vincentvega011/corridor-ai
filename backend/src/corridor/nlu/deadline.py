"""Deterministic deadline resolver (P3-1, ADR 0004 item 9).

The parser model only ever hints at a deadline. This module turns the user's own words into a date with
plain code, so the same message always gives the same date and a phrase we cannot resolve is never
guessed: it returns ``NEEDS_CLARIFICATION`` and the agent asks (INV-9, INV-10).

Resolution order: (1) phrase classes added here (within N days or weeks, end of the month, the next
semester start, Bikram Sambat dates, vague event words), then (2) what the normaliser already found
(festivals from ``data/calendar/festivals.yaml``, explicit dates, relative days, weekdays, periods).

Conventions, fixed in ADR 0004 and in ``eval/nlu/deadline_holdout.jsonl`` before this code existed:
"before <festival>" is on or before the festival's main day ("before Dashain" is Vijaya Dashami,
"before Dashain starts" is Ghatasthapana); "next Friday" is the Friday of next Monday to Sunday week;
"end of the month" is the last day of the current month; "within N days" is today plus N days. The
deadline is the end of that day (23:59:59) in the user's time zone. Bikram Sambat dates are not
supported (no verified month length table), so they ask instead of converting.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from corridor.nlu.normalise import Normalised

DATA = Path(__file__).resolve().parents[4] / "data" / "calendar"
DEFAULT_TZ = "Asia/Kathmandu"
MAX_DAYS = 366
_DEVA_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


class DeadlineStatus(StrEnum):
    RESOLVED = "resolved"
    NEEDS_CLARIFICATION = "needs_clarification"
    NONE = "none"  # the message carries no deadline cue at all


@dataclass(frozen=True)
class DeadlineResolution:
    """Result of one resolution. ``on`` is the day, ``kind`` names the phrase class, ``reason`` says why we ask."""

    status: DeadlineStatus
    kind: str | None = None
    on: date | None = None
    at: datetime | None = None  # end of ``on`` in the user's time zone
    phrase: str | None = None
    reason: str | None = None


@lru_cache(maxsize=1)
def _phrases() -> dict[str, Any]:
    raw = yaml.safe_load((DATA / "deadline_phrases.yaml").read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("deadline_phrases.yaml must be a mapping")
    return raw


@lru_cache(maxsize=1)
def _university() -> dict[str, Any]:
    raw = yaml.safe_load((DATA / "university.yaml").read_text(encoding="utf-8"))
    unis = raw["universities"]
    return next((u for u in unis.values() if u.get("default")), {})


def _first(patterns: list[str], text: str) -> re.Match[str] | None:
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m
    return None


def _end_of(d: date, tz: str) -> datetime:
    return datetime.combine(d, time(23, 59, 59), tzinfo=ZoneInfo(tz))


def _resolved(kind: str, d: date, tz: str, phrase: str) -> DeadlineResolution:
    return DeadlineResolution(DeadlineStatus.RESOLVED, kind, d, _end_of(d, tz), phrase)


def _ask(reason: str, phrase: str | None = None) -> DeadlineResolution:
    return DeadlineResolution(DeadlineStatus.NEEDS_CLARIFICATION, None, None, None, phrase, reason)


def _is_bikram_sambat(text: str, ph: dict[str, Any]) -> str | None:
    cfg = ph["bikram_sambat_markers"]
    for m in re.finditer(r"(?<!\d)(\d{4})(?!\d)", text):
        if cfg["year_min"] <= int(m.group(1)) <= cfg["year_max"]:
            return m.group(0)
    for pat in cfg["markers"]:
        hit = re.search(pat, text)
        if hit and re.search(r"\d", text):
            return hit.group(0)
    return None


def resolve_deadline(
    text: str, today: date, tz: str = DEFAULT_TZ, norm: Normalised | None = None
) -> DeadlineResolution:
    """Resolve the deadline in ``text`` (the user's own typed words, INV-3) as of ``today``.

    ``norm`` is the normaliser output for the same text; when given, its festival and date findings are
    used after the phrase classes defined here. Never raises on odd input: anything unclear asks."""
    ph = _phrases()
    t = text.lower().translate(_DEVA_DIGITS)
    bs = _is_bikram_sambat(t, ph)
    if bs:
        return _ask("bikram_sambat_unsupported", bs)
    m = _first(ph["within_days"], t)
    if m:
        n = int(m.group("n"))
        if not 1 <= n <= MAX_DAYS:
            return _ask("days_out_of_range", m.group(0))
        return _resolved("within_days", today + timedelta(days=n), tz, m.group(0))
    m = _first(ph["within_weeks"], t)
    if m:
        n = int(m.group("n"))
        if not 1 <= n * 7 <= MAX_DAYS:
            return _ask("days_out_of_range", m.group(0))
        return _resolved("within_days", today + timedelta(days=7 * n), tz, m.group(0))
    m = _first(ph["end_of_month"], t)
    if m:
        nxt = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        return _resolved("end_of_month", nxt - timedelta(days=1), tz, m.group(0))
    m = _first(ph["semester_start"], t)
    if m:
        starts = sorted(
            date.fromisoformat(str(s)) for s in _university().get("semester_starts", [])
        )
        nxt_start = next((s for s in starts if s > today), None)
        if nxt_start is None:
            return _ask("semester_start_unknown", m.group(0))
        return _resolved("semester_start", nxt_start, tz, m.group(0))
    m = _first(ph["unknown_event"], t)
    if m:
        return _ask("unknown_event", m.group(0))
    if norm is not None:
        fest = next(iter(norm.festivals), None)
        if fest is not None:
            if fest.date is None:
                return _ask("festival_date_unknown", fest.span.text)
            return _resolved(f"festival_{fest.deadline_kind}", fest.date, tz, fest.span.text)
        dated = [d for d in norm.dates if d.date is not None and not d.is_past]
        days = {d.date for d in dated}
        if len(days) > 1:
            return _ask("several_dates", None)
        if dated and dated[0].date is not None:
            d = dated[0]
            if d.ambiguous:
                return _ask("date_order_ambiguous", d.span.text)
            assert d.date is not None
            return _resolved(d.kind.value, d.date, tz, d.span.text)
    return DeadlineResolution(DeadlineStatus.NONE)
