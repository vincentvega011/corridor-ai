"""INV-12 prompt guard: nothing sensitive may leave the process in a prompt.

The guard runs on every request (live, record and replay) before any network call. Text is
first normalised (NFKC, invisible format characters such as zero-width spaces removed), then
checked against these rules:
- R-GUARD-DIGITS: 9 or more digits in a run, where any mix of spaces, hyphens, dots, slashes,
  underscores and newlines between digits is ignored (account, card and phone numbers). Masked
  forms such as ``****1234`` pass. Benign shapes are removed first: ISO dates and times, UUIDs
  and long hex hashes, and comma grouped money next to a currency code (at most 2 comma groups,
  for example ``NPR 1,000,000.00``). Prompt builders must render money that way, never as a bare
  integer of 9 or more digits, and must not print long digit lists.
- R-GUARD-KEY: API key shapes (sk-..., AKIA..., bearer tokens, JWTs, PEM private keys).
- R-GUARD-TOTP: base32 strings of 16 or more characters (TOTP seeds), including the spaced
  upper case groups of four that authenticator apps display.
- R-GUARD-SECRET: any value registered in the SecretRegistry.
- R-GUARD-STRUCT: a message with unexpected keys, content types or part types. This closes
  the route of hiding a value in a field the guard does not read.

A hit raises PromptLeakError carrying only the rule id. Content is never logged or echoed.
"""

import json
import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from corridor.llm.errors import PromptLeakError

_ALLOWED_KEYS = frozenset({"role", "content", "tool_calls", "tool_call_id"})
_ALLOWED_PARTS = frozenset({"text", "image_url"})
_CUR = r"(?:NPR|AUD|QAR|MYR|CNY|USD)"
_BENIGN = (
    re.compile(
        r"(?<!\d)(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
        r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?(?!\d)"
    ),
    re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
    re.compile(r"\b(?=[0-9a-f]*[a-f])[0-9a-f]{32,}\b"),
    re.compile(rf"\b{_CUR}\s?\d{{1,3}}(?:,\d{{3}}){{1,2}}(?:\.\d{{1,2}})?(?!\d|,\d)"),
    re.compile(rf"(?<![\d,.])\d{{1,3}}(?:,\d{{3}}){{1,2}}(?:\.\d{{1,2}})?\s?{_CUR}\b"),
)
_DIGIT_SEQ = re.compile(r"\d(?:[\W_]*\d)+")
_KEY_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]*"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
_TOTP_CONTIGUOUS = re.compile(r"(?<![A-Za-z0-9])[A-Za-z2-7]{16,}=*(?![A-Za-z0-9])")
_TOTP_SPACED = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z2-7]{4}[\s\-.]){3,}[A-Za-z2-7]{4}(?![A-Za-z0-9])"
)
_SEPARATORS = re.compile(r"[\s\-.]")
MIN_SECRET_LEN = 6


class SecretRegistry:
    """Values that must never appear in a prompt (MFA seeds, tokens, full account numbers)."""

    def __init__(self, values: Iterable[str] = ()) -> None:
        self._values: set[str] = set()
        for v in values:
            self.register(v)

    def register(self, value: str) -> None:
        """Register a secret. Values under 6 characters are rejected (too many false hits)."""
        if len(value) < MIN_SECRET_LEN:
            raise ValueError("secret too short to register safely")
        self._values.add(value)

    def contains_secret(self, text: str) -> bool:
        """True if any registered secret occurs in ``text``, also ignoring case and separators."""
        squashed = _SEPARATORS.sub("", text).lower()
        return any(v in text or _SEPARATORS.sub("", v).lower() in squashed for v in self._values)


def normalise(text: str) -> str:
    """NFKC normalise and drop invisible format characters (zero-width spaces, joiners)."""
    folded = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")


def _digits_hit(text: str) -> bool:
    for pat in _BENIGN:
        text = pat.sub(" ", text)
    return any(sum(ch.isdigit() for ch in m.group()) >= 9 for m in _DIGIT_SEQ.finditer(text))


def redact_digit_runs(text: str, placeholder: str = "[LONG-NUMBER]") -> str:
    """Replace every digit run that the guard would block (9 or more digits across separators,
    after the same normalisation and benign shape masking) by ``placeholder``.

    Returns the NFKC normalised text. Applying the guard's own logic means that after redaction
    ``scan_text`` can no longer fail on digits, and benign shapes (ISO dates, comma grouped money
    next to a currency code, UUIDs) are kept intact."""
    norm = normalise(text)
    masked = norm
    for pat in _BENIGN:
        masked = pat.sub(lambda m: " " * len(m.group()), masked)
    out: list[str] = []
    last = 0
    for m in _DIGIT_SEQ.finditer(masked):
        if sum(ch.isdigit() for ch in m.group()) >= 9:
            out.append(norm[last : m.start()])
            out.append(placeholder)
            last = m.end()
    out.append(norm[last:])
    return "".join(out)


_ACCOUNT_RUN = re.compile(r"\d(?:[ \-_/.,]?\d)+")


def mask_account_numbers(text: str, placeholder: str = "[ACCOUNT]") -> str:
    """Replace anything that looks like an account number by ``placeholder`` before a model
    sees the text.

    INV-12, defence in depth under the 9 digit guard: a run of 6 or more digits, a BSB
    (``123-456``) and digit
    groups joined by single spaces or hyphens that together reach 6 digits (``0620 0077 1234``,
    ``062-000
    12345678``, a 16 digit card number). ISO dates, UUIDs, hashes and comma grouped money next
    to a currency
    code are left alone. A plain amount of 6 or more digits is masked as well: the
    deterministic normaliser
    reads the amount from the original text, so the model still gets it as a computed hint. Pure and
    deterministic; the original text stays in code (the typed payee origin check reads it there)."""
    norm = normalise(text)
    masked = norm
    for pat in _BENIGN:
        masked = pat.sub(lambda m: " " * len(m.group()), masked)
    out: list[str] = []
    last = 0
    for m in _ACCOUNT_RUN.finditer(masked):
        if sum(ch.isdigit() for ch in m.group()) >= 6:
            out.append(norm[last : m.start()])
            out.append(placeholder)
            last = m.end()
    out.append(norm[last:])
    return "".join(out)


def _totp_hit(text: str) -> bool:
    for m in _TOTP_SPACED.finditer(text):  # authenticator style groups of four
        token = _SEPARATORS.sub("", m.group())
        if token.isupper() or any(ch.isdigit() for ch in token):
            return True
    for m in _TOTP_CONTIGUOUS.finditer(text):
        token = m.group().rstrip("=")
        if token.isupper() or any(ch.isdigit() for ch in token):
            return True
    return False


def scan_text(text: str, registry: SecretRegistry) -> None:
    """Raise PromptLeakError if ``text`` trips any rule."""
    norm = normalise(text)
    if registry.contains_secret(text) or registry.contains_secret(norm):
        raise PromptLeakError("R-GUARD-SECRET")
    if _digits_hit(norm):
        raise PromptLeakError("R-GUARD-DIGITS")
    if any(p.search(norm) for p in _KEY_PATTERNS):
        raise PromptLeakError("R-GUARD-KEY")
    if _totp_hit(norm):
        raise PromptLeakError("R-GUARD-TOTP")


_SOURCE_HASH = re.compile(r"[0-9a-f]{64}")


def _scan_message(m: dict[str, Any], registry: SecretRegistry) -> None:
    if not set(m) <= _ALLOWED_KEYS:
        raise PromptLeakError("R-GUARD-STRUCT")
    content = m.get("content")
    if isinstance(content, str):
        scan_text(content, registry)
    elif isinstance(content, list):
        for part in content:
            if not isinstance(part, dict) or part.get("type") not in _ALLOWED_PARTS:
                raise PromptLeakError("R-GUARD-STRUCT")
            if part["type"] == "text":
                if set(part) != {"type", "text"} or not isinstance(part["text"], str):
                    raise PromptLeakError("R-GUARD-STRUCT")
                scan_text(part["text"], registry)
            elif not (
                set(part) <= {"type", "image_url", "source_sha256"} and "image_url" in part
            ):  # image bytes are not text-scanned
                raise PromptLeakError("R-GUARD-STRUCT")
            elif "source_sha256" in part and not _SOURCE_HASH.fullmatch(str(part["source_sha256"])):
                # key-only field (stripped before sending): it can carry nothing but a SHA-256
                raise PromptLeakError("R-GUARD-STRUCT")
    elif content is not None:
        raise PromptLeakError("R-GUARD-STRUCT")
    if isinstance(m.get("tool_call_id"), str):
        scan_text(m["tool_call_id"], registry)
    for call in m.get("tool_calls") or []:
        scan_text(json.dumps(call, ensure_ascii=False, default=str), registry)


def scan_messages(
    messages: Iterable[dict[str, Any]], registry: SecretRegistry, skip_first: int = 0
) -> None:
    """Scan caller supplied messages. ``skip_first`` skips gateway generated leading messages
    (the schema instruction), never anything a caller or a model wrote."""
    for i, m in enumerate(messages):
        if i < skip_first:
            continue
        _scan_message(m, registry)


def scan_tools(tools: Iterable[Any], registry: SecretRegistry) -> None:
    """Scan tool names, descriptions and argument schemas, which are also sent to the model."""
    for t in tools:
        scan_text(f"{t.name}\n{t.description}\n{json.dumps(t.args_schema, default=str)}", registry)
