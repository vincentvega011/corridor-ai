"""Record and replay of model calls (docs/spec/modules.md 5.2, 7.4).

One cassette file per HTTP level request: ``<dir>/<role>/<sha256 of canonical request>.json``.
The canonical request is role, model, messages, schema name, full tool specs, temperature,
max_tokens, extra_body and structured mode, using
``corridor.contracts.canonical``. Only successful responses are recorded. A repair retry has
different messages and therefore its own cassette, so repair paths replay faithfully.
"""

import json
import re
from pathlib import Path
from typing import Any

from corridor.contracts.canonical import sha256_hex
from corridor.llm.errors import CassetteMiss

DEFAULT_CASSETTE_DIR = Path(__file__).resolve().parents[4] / "eval" / "cassettes"


# Ids that are random per run (transfer ids from random idempotency keys, proposal ids). They
# appear in tool results fed back to a model, so two runs of the same conversation would never share
# a cassette entry. The KEY treats each as a placeholder; nothing else is normalised.
_VOLATILE = re.compile(
    r"\b(?:tr_[0-9a-f]{20}|btr_[0-9a-f]{16}|idem-[0-9a-f]{32}"
    r"|prop_[0-9a-f]{8,16}|pay_doc_[0-9a-f]{16})\b"
)


def _stable_part(part: Any) -> Any:
    """An image part with ``source_sha256`` is keyed by the original upload, not its pixels."""
    if isinstance(part, dict) and part.get("source_sha256") and part.get("type") == "image_url":
        return {"type": "image_url", "source_sha256": part["source_sha256"]}
    return part


def wire_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Messages as sent to a provider: the key-only ``source_sha256`` field is removed."""
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            c = [
                {k: v for k, v in p.items() if k != "source_sha256"} if isinstance(p, dict) else p
                for p in c
            ]
            m = {**m, "content": c}
        out.append(m)
    return out


def _stable(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            out.append({**m, "content": _VOLATILE.sub("<volatile-id>", c)})
        elif isinstance(c, list):
            out.append({**m, "content": [_stable_part(p) for p in c]})
        else:
            out.append(m)
    return out


def request_key(
    role: str,
    model: str,
    messages: list[dict[str, Any]],
    schema_name: str | None,
    tools: list[dict[str, Any]],
    temperature_milli: int,
    params: dict[str, Any],
) -> str:
    """Return the cassette key for a request."""
    return sha256_hex(
        {
            "role": role,
            "model": model,
            "messages": _stable(messages),
            "schema": schema_name,
            "tools": sorted(tools, key=lambda t: str(t["name"])),
            "temperature_milli": temperature_milli,
            "params": params,
        }
    )


class CassetteStore:
    """Reads and writes cassette files under ``root``."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or DEFAULT_CASSETTE_DIR

    def _path(self, role: str, key: str) -> Path:
        return self.root / role / f"{key}.json"

    def write(self, role: str, key: str, response: dict[str, Any]) -> None:
        """Persist a successful response."""
        path = self._path(role, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"key": key, "role": role, "response": response}, ensure_ascii=False),
            encoding="utf-8",
        )

    def read(self, role: str, key: str) -> dict[str, Any]:
        """Return the recorded response or raise CassetteMiss (fail closed)."""
        path = self._path(role, key)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            resp = data["response"]
        except (OSError, ValueError, KeyError) as exc:
            raise CassetteMiss(key, role) from exc
        if data.get("key") != key or not isinstance(resp, dict):
            raise CassetteMiss(key, role)
        return resp
