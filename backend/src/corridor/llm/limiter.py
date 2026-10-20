"""Client-side rate limiter shared by all roles of a provider, persisted across restarts.

The state file (default ``logs/quota.json``) holds the millisecond timestamps of recent requests
per provider plus an optional cool-down set after a 429. Windows are sliding: one minute and one
hour. Limits are set below the published ones (modules 5.2, models.yaml). When a window is full,
a wait of at most ``max_wait_s`` is slept once; a longer wait raises QuotaExhausted so the caller
can fail over instead of stalling or looping. A file lock makes concurrent processes (eval
workers, dev servers) share one budget. A corrupt state file is moved aside and counting restarts.
"""

import asyncio
import fcntl
import json
import os
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import structlog

from corridor.llm.config import Limits
from corridor.llm.errors import QuotaExhausted

log = structlog.get_logger("corridor.llm.limiter")

DEFAULT_QUOTA_PATH = Path(__file__).resolve().parents[4] / "logs" / "quota.json"
MINUTE_MS = 60_000
HOUR_MS = 3_600_000


class RateLimiter:
    """Sliding window limiter with persisted state."""

    def __init__(
        self,
        limits: dict[str, Limits],
        path: Path | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_wait_s: int = 10,
    ) -> None:
        self.limits = limits
        self.path = path or DEFAULT_QUOTA_PATH
        self._clock = clock
        self._sleep = sleep
        self.max_wait_s = max_wait_s

    def _now_ms(self) -> int:
        return int(self._clock() * 1000)

    @contextmanager
    def _locked_state(self) -> Iterator[dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(".lock")
        with open(lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                state = self._load()
                yield state
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(state), encoding="utf-8")
                os.replace(tmp, self.path)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _load(self) -> dict[str, Any]:
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if self._valid(state):
                return dict(state)
        except FileNotFoundError:
            return {"providers": {}}
        except (OSError, ValueError):
            pass
        with suppress(OSError):
            self.path.rename(self.path.with_suffix(".corrupt"))
        log.warning("quota_state_corrupt_reset")
        return {"providers": {}}

    @staticmethod
    def _valid(state: Any) -> bool:
        if not isinstance(state, dict) or not isinstance(state.get("providers"), dict):
            return False
        for entry in state["providers"].values():
            calls = entry.get("calls_ms", []) if isinstance(entry, dict) else None
            cool = entry.get("cooldown_until_ms", 0) if isinstance(entry, dict) else None
            if not isinstance(calls, list) or not all(type(t) is int for t in calls):
                return False
            if type(cool) is not int:
                return False
        return True

    @staticmethod
    def _entry(state: dict[str, Any], provider: str) -> dict[str, Any]:
        entry: dict[str, Any] = state["providers"].setdefault(provider, {})
        entry.setdefault("calls_ms", [])
        entry.setdefault("cooldown_until_ms", 0)
        return entry

    def _wait_ms(self, entry: dict[str, Any], limits: Limits, now: int) -> int:
        """Milliseconds until a request is allowed (0 means now)."""
        wait = max(0, int(entry["cooldown_until_ms"]) - now)
        calls: list[int] = entry["calls_ms"]
        for window, cap in (
            (MINUTE_MS, limits.requests_per_minute),
            (HOUR_MS, limits.requests_per_hour),
        ):
            if cap <= 0:
                continue
            recent = sorted(t for t in calls if t > now - window)
            if len(recent) >= cap:
                wait = max(wait, recent[len(recent) - cap] + window - now)
        return wait

    async def acquire(self, provider: str) -> None:
        """Take one request slot or raise QuotaExhausted. Sleeps at most once, up to max_wait_s."""
        limits = self.limits.get(provider)
        if limits is None or (limits.requests_per_minute <= 0 and limits.requests_per_hour <= 0):
            return
        slept = False
        while True:
            now = self._now_ms()
            with self._locked_state() as state:
                entry = self._entry(state, provider)
                entry["calls_ms"] = [t for t in entry["calls_ms"] if t > now - HOUR_MS]
                wait_ms = self._wait_ms(entry, limits, now)
                if wait_ms <= 0:
                    entry["calls_ms"].append(now)
                    return
            wait_s = -(-wait_ms // 1000)
            if slept or wait_s > self.max_wait_s:
                log.warning("quota_exhausted", provider=provider, wait_s=wait_s)
                raise QuotaExhausted(f"{provider} quota exhausted; next slot in {wait_s}s", wait_s)
            slept = True
            await self._sleep(wait_s)

    def cool_down(self, provider: str, seconds: int) -> None:
        """Block the provider for ``seconds`` (set after a 429 so later calls fail fast)."""
        now = self._now_ms()
        with self._locked_state() as state:
            entry = self._entry(state, provider)
            entry["cooldown_until_ms"] = max(int(entry["cooldown_until_ms"]), now + seconds * 1000)

    def usage(self, provider: str) -> tuple[int, int]:
        """Return (requests in the last minute, requests in the last hour)."""
        now = self._now_ms()
        with self._locked_state() as state:
            calls = self._entry(state, provider)["calls_ms"]
            return (
                sum(1 for t in calls if t > now - MINUTE_MS),
                sum(1 for t in calls if t > now - HOUR_MS),
            )
