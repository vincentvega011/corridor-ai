"""The baseline's own log, clearly marked, written to logs/baseline.jsonl (never the ledger)."""

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from corridor.config import REPO_ROOT

DEFAULT_PATH = REPO_ROOT / "logs" / "baseline.jsonl"
_LOCK = threading.Lock()


def write(event: str, data: dict[str, Any], path: Path | None = None) -> None:
    """Append one JSON line. Every line carries ``"agent": "BASELINE"`` so it is never confused with the ledger."""
    target = path or DEFAULT_PATH
    line = {
        "agent": "BASELINE",
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "event": event,
        **data,
    }
    with _LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
