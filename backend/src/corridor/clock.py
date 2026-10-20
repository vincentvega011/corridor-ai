"""The sandbox clock. Normally the real time; in demo mode a fixed documented date that then moves
at the real rate (so grants still expire and TOTP windows still work).

Demo date: 2026-10-10 09:00 Nepal time, eleven days before Vijaya Dashami (2026-10-21) and the
day before
Ghatasthapana (2026-10-11), so "before Dashain" has a meaningful deadline and the festival calendar
(``data/calendar/festivals.yaml``) is in range. It is the same "today" the NLU evaluation uses.
Allowed only
in dev and demo: the UI shows a banner whenever it is on (``GET /clock``)."""

import time
from datetime import UTC, date, datetime, timedelta, timezone

DEMO_DATE_ISO = "2026-10-10T09:00:00+05:45"
DEMO_LABEL = "Demo date: 10 Oct 2026 (before Dashain)"
_NEPAL = timezone(timedelta(hours=5, minutes=45))


class SandboxClock:
    """``now()`` is real time, or a fixed start that advances at the real rate."""

    def __init__(self, fixed: datetime | None = None, label: str | None = None) -> None:
        if fixed is not None and fixed.tzinfo is None:
            raise ValueError("demo clock needs a time zone")
        self.fixed = fixed
        self.label = label if fixed is not None else None
        self._start = time.monotonic()

    @property
    def demo(self) -> bool:
        return self.fixed is not None

    def now(self) -> datetime:
        if self.fixed is None:
            return datetime.now(UTC)
        return (self.fixed + timedelta(seconds=time.monotonic() - self._start)).astimezone(UTC)

    def today(self) -> date:
        return self.now().astimezone(_NEPAL if self.demo else UTC).date()


def clock_from(value: str | None) -> SandboxClock:
    """``DEMO_CLOCK=on`` uses the documented date; an ISO time with zone sets another fixed time."""
    if not value or value.lower() in ("off", "0", "false"):
        return SandboxClock()
    iso = DEMO_DATE_ISO if value.lower() in ("on", "1", "true") else value
    return SandboxClock(
        datetime.fromisoformat(iso), DEMO_LABEL if iso == DEMO_DATE_ISO else "Demo clock"
    )
