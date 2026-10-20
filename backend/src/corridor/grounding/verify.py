"""The grounding verifier (docs/spec/modules.md 5.14, INV-9).

``verify_reply`` extracts every number from a reply and checks each against the registry of facts from
the same turn: money exactly in minor units after rounding, rates to 4 significant digits, percentages
as basis points, dates and counts exactly. ``ground_reply`` wraps the policy: a failing reply is
regenerated once with a stricter instruction; if that fails too, a templated reply built only from
registry facts is returned. The outcome is reported as ``pass``, ``regenerated`` or ``templated``.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

import structlog

from corridor.grounding.extract import Found, Kind, extract_numbers
from corridor.grounding.registry import ToolResultRegistry, sig4

log = structlog.get_logger("corridor.grounding")


@dataclass(frozen=True)
class GroundingResult:
    """Whether a reply is fully grounded, and which numbers were not."""

    ok: bool
    ungrounded: tuple[str, ...] = ()
    checked: int = 0


def _grounded(f: Found, reg: ToolResultRegistry) -> bool:
    if f.kind is Kind.MONEY and f.minor is not None:
        if f.ambiguous_currency or f.currency is None:
            # a bare "$" can only mean a dollar currency we support
            return any(m == f.minor and c in ("USD", "AUD") for m, c in reg.money)
        return (f.minor, f.currency.value) in reg.money
    if f.kind is Kind.PERCENT and f.value is not None:
        return int((f.value * 100).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)) in reg.bps
    if f.kind is Kind.RATE and f.value is not None:
        return sig4(f.value) in {sig4(r) for r in reg.rates} or sig4(f.value) in reg.rates
    if f.kind is Kind.DATE:
        if f.on is None and f.value is not None:  # no year written: month and day must match a fact
            return any(d.month * 100 + d.day == int(f.value) for d in reg.dates)
        return f.on in reg.dates
    if f.kind is Kind.COUNT and f.value is not None:
        return int(f.value) in reg.counts
    if f.kind is Kind.NUMBER and f.value is not None:
        return (
            f.value == f.value.to_integral_value() and int(f.value) in reg.counts
        ) or f.value in reg.majors()
    return False


def verify_reply(reply: str, registry: ToolResultRegistry) -> GroundingResult:
    """Check every number in ``reply`` against the registry."""
    found = extract_numbers(reply)
    bad = tuple(f.raw.strip() for f in found if not _grounded(f, registry))
    return GroundingResult(ok=not bad, ungrounded=bad, checked=len(found))


SAFE_SENTENCE = "I cannot show exact figures for that. Please check your accounts screen."
SAFE_EXECUTED = "Your payment was completed. Please check your transactions screen."
Regenerate = Callable[[tuple[str, ...]], Awaitable[str | None]]


async def ground_reply(
    reply: str,
    registry: ToolResultRegistry,
    *,
    regenerate: Regenerate | None,
    fallback: Callable[[], str],
    safe: str | None = None,
) -> tuple[str, str, GroundingResult, list[GroundingResult]]:
    """Return ``(reply, outcome, final result, all attempts)``.

    ``outcome`` is ``pass``, ``regenerated`` or ``templated``. The templated reply comes from
    ``fallback`` and is itself verified; if even that fails, a fixed digit-free sentence (``safe``,
    or ``SAFE_SENTENCE``) is returned."""
    first = verify_reply(reply, registry)
    attempts = [first]
    if first.ok:
        return reply, "pass", first, attempts
    log.warning("grounding_failed", attempt=1, ungrounded=len(first.ungrounded))
    if regenerate is not None:
        try:
            retry = await regenerate(first.ungrounded)
        except Exception as exc:
            log.warning("grounding_regenerate_error", error=type(exc).__name__)
            retry = None
        if retry:
            second = verify_reply(retry, registry)
            attempts.append(second)
            if second.ok:
                return retry, "regenerated", second, attempts
            log.warning("grounding_failed", attempt=2, ungrounded=len(second.ungrounded))
    templated = fallback()
    final = verify_reply(templated, registry)
    if not final.ok:  # even the fallback states an ungrounded figure: say nothing numeric
        templated = safe or SAFE_SENTENCE
        final = verify_reply(templated, registry)
    return templated, "templated", final, attempts
