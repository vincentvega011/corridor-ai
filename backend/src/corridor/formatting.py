"""Human readable rendering of money, rates and dates for tools, prompts and replies.

Security reason (INV-12 and the prompt guard): a raw minor-unit integer such as 100000000 (NPR
1,000,000.00) is a 9 digit run and is indistinguishable from an account number, so the prompt guard
blocks it. Everything that can reach a prompt is therefore rendered as ``NPR 1,000,000.00``. The
exact integer still travels in ``ToolResult.facts`` for the grounding registry, never in prompt
text. Rates are shown to 4 significant digits, exactly what the grounding verifier compares;
the full 8 place value lives in the facts.
"""

from datetime import UTC, date, datetime, tzinfo
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from corridor.contracts.money import CurrencyCode, Money, minor_unit_exponent


def format_minor(amount_minor: int, currency: CurrencyCode) -> str:
    """Render minor units as ``NPR 1,000,000.00`` (negative: ``-NPR 5.00``)."""
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
        raise TypeError("amount_minor must be an int")
    exp = minor_unit_exponent(currency)
    sign = "-" if amount_minor < 0 else ""
    major, minor = divmod(abs(amount_minor), 10**exp)
    return f"{sign}{currency.value} {major:,}.{minor:0{exp}d}"


def format_money(money: Money) -> str:
    """Render a Money value, see ``format_minor``."""
    return format_minor(money.amount_minor, money.currency)


def format_rate(rate: Decimal) -> str:
    """Render a rate to 4 significant digits (banker's rounding), for example ``106.9``.

    Grounding compares rates at 4 significant digits (INV-9), so the display never shows more
    precision than the verifier checks. The full 8 place value stays in the facts."""
    if rate == 0:
        return "0"
    with localcontext() as ctx:
        ctx.prec = 60
        exponent = rate.adjusted()
        return f"{rate.quantize(Decimal(1).scaleb(exponent - 3), rounding=ROUND_HALF_EVEN):f}"


def format_bps(bps: int) -> str:
    """Render basis points as a percentage with 2 decimals, for example 125 -> ``1.25%``."""
    whole, frac = divmod(bps, 100)
    return f"{whole}.{frac:02d}%"


def format_date(d: date) -> str:
    """ISO date, which the prompt guard treats as benign."""
    return d.isoformat()


_DEVA_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")


def format_local_time(iso: str, tz: str | None, *, deva: bool = False) -> str:
    """A UTC (or offset) ISO time shown in the user's time zone with the zone named.

    ``2026-10-12T16:50+00:00`` for ``Australia/Sydney`` gives
    ``2026-10-13 03:50 UTC+11:00 (Australia/Sydney)``. With no or an unknown zone the time is shown
    in UTC and says so. Devanagari digits when ``deva``."""
    try:
        at = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    zone: tzinfo
    try:
        zone, name = (ZoneInfo(tz), tz) if tz else (UTC, "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        zone, name = UTC, "UTC"
    local = at.astimezone(zone)
    off = local.utcoffset()
    minutes = int(off.total_seconds() // 60) if off is not None else 0
    sign = "+" if minutes >= 0 else "-"
    text = (
        f"{local:%Y-%m-%d %H:%M} UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d} ({name})"
    )
    return text.translate(_DEVA_DIGITS) if deva else text
