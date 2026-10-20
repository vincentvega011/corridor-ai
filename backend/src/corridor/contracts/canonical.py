"""Canonical JSON and SHA-256 hashing (RFC 8785 style).

Security reason (INV-5, INV-8): a confirmation grant is bound to the hash of the exact
proposal, and the ledger chains hashes of payloads. Both need one and only one byte
representation of a value, independent of dict ordering, whitespace or Python version.

Follows RFC 8785 (JSON Canonicalization Scheme): no insignificant whitespace, object keys
sorted by UTF-16 code units, UTF-8 output, minimal string escaping.

Deviations from RFC 8785, all deliberate:
- Floats are rejected (TypeError). Money is integers; rates are Decimals. JCS number
  formatting for floats is a source of cross-language drift we do not need.
- Integers are written exactly, even beyond 2**53 (JCS would route them via IEEE 754).
- Decimals are written as normalised strings (no exponent, no trailing zeros).
- Datetimes must be timezone aware and are written as UTC ISO 8601 with a trailing Z.
- Enums are written as their values; Pydantic models via ``model_dump(mode="python")``.
- Sets, bytes and other unordered or binary types are rejected.
"""

import hashlib
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from enum import Enum
from typing import Any

from pydantic import BaseModel


def _dumps_str(s: str) -> str:
    import json

    return json.dumps(s, ensure_ascii=False)


def _decimal_str(d: Decimal) -> str:
    if not d.is_finite():
        raise ValueError("cannot canonicalise a non finite Decimal")
    if d == 0:
        return "0"
    with localcontext() as ctx:
        # Default 28 digit precision would silently round long values; never round here.
        ctx.prec = max(len(d.as_tuple().digits) + 2, 28)
        return format(d.normalize(), "f")


def _datetime_str(dt: datetime) -> str:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("cannot canonicalise a naive datetime; use timezone aware values")
    utc = dt.astimezone(UTC)
    out = utc.strftime("%Y-%m-%dT%H:%M:%S")
    if utc.microsecond:
        out += "." + f"{utc.microsecond:06d}".rstrip("0")
    return out + "Z"


def _ser(obj: Any) -> str:
    if isinstance(obj, Enum):
        return _ser(obj.value)
    if obj is None:
        return "null"
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if isinstance(obj, int):
        return str(int(obj))
    if isinstance(obj, float):
        raise TypeError("floats are not allowed in canonical JSON (money is integer minor units)")
    if isinstance(obj, str):
        return _dumps_str(obj)
    if isinstance(obj, Decimal):
        return _dumps_str(_decimal_str(obj))
    if isinstance(obj, datetime):
        return _dumps_str(_datetime_str(obj))
    if isinstance(obj, date):
        return _dumps_str(obj.isoformat())
    if isinstance(obj, BaseModel):
        return _ser(obj.model_dump(mode="python"))
    if isinstance(obj, Mapping):
        items: dict[str, Any] = {}
        for k, v in obj.items():
            key = k.value if isinstance(k, Enum) else k
            if not isinstance(key, str):
                raise TypeError("canonical JSON object keys must be strings")
            if key in items:
                raise ValueError(f"duplicate object key after normalisation: {key!r}")
            items[key] = v
        ordered = sorted(items, key=lambda k: k.encode("utf-16-be"))
        return "{" + ",".join(f"{_dumps_str(k)}:{_ser(items[k])}" for k in ordered) + "}"
    if isinstance(obj, list | tuple):
        return "[" + ",".join(_ser(v) for v in obj) + "]"
    raise TypeError(f"type {type(obj).__name__} is not canonicalisable")


def canonical_json(obj: Any) -> str:
    """Return the canonical JSON text of ``obj``."""
    return _ser(obj)


def canonical_bytes(obj: Any) -> bytes:
    """Return the canonical JSON of ``obj`` encoded as UTF-8."""
    return canonical_json(obj).encode("utf-8")


def sha256_hex(obj: Any) -> str:
    """Return the lowercase hex SHA-256 of the canonical JSON of ``obj``."""
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()
