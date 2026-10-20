"""Detect pasted or forwarded third party content inside a chat message (docs/spec/modules.md 5.5, INV-3).

The user's own words go to the parser. Anything that looks like someone else's message goes only to the
quarantined reader as UNTRUSTED data. Detection is deterministic (patterns in ``data/lang``):

- explicit structure (certain): quoted lines (``>``), header lines (``From:``, ``Fwd:``, ``SMS:``) and
  everything after one, text between triple quotes or backticks, a separately supplied forwarded box;
- another person's voice (uncertain): phrases such as "dear customer" or "send me", or an imperative
  together with an account number or a URL.

If anything is only heuristic, or nothing of the user's own voice is left, the split is *uncertain*:
the caller must treat payee, account and amount values as untrusted and ask the user to confirm the
recipient from their saved payees.
"""

import re
from dataclasses import dataclass

from corridor.reader.patterns import (
    Patterns,
    account_candidates,
    has_url,
    load_patterns,
    squash,
)

_FENCE = re.compile(r"^\s*(\"\"\"|```|'''|-----+)\s*$")
_SMS_HEADER = re.compile(r"^\s*\[?\d{1,2}[:.]\d{2}[^\]:]{0,40}\]?\s*[\w .()-]{1,40}:\s+\S")
_MAX_PART = 2000


@dataclass(frozen=True)
class MessageSplit:
    """The user's own text, the pasted parts and how sure the split is."""

    user_text: str
    pasted: tuple[str, ...]
    certain: bool
    reasons: tuple[str, ...]

    @property
    def has_pasted(self) -> bool:
        return bool(self.pasted)


def split_message(
    text: str, forwarded: str | None = None, patterns: Patterns | None = None
) -> MessageSplit:
    """Split ``text`` into the user's own words and pasted third party content."""
    p = patterns or load_patterns()
    pasted: list[str] = []
    reasons: list[str] = []
    certain = True
    if forwarded and forwarded.strip():
        pasted.append(forwarded.strip()[:_MAX_PART])
        reasons.append("explicit_forwarded_box")
    user_lines: list[str] = []
    block: list[str] = []
    fenced = False
    after_header = False
    fence_lines = 0
    pasted_started = False  # once pasted structure began, later unmarked lines are ambiguous
    user_after_paste = False
    for line in text.splitlines():
        low = line.strip().casefold()
        if _FENCE.match(line):
            fence_lines += 1
            if fenced and block:
                pasted.append("\n".join(block))
                block = []
            fenced = not fenced
            pasted_started = True
            reasons.append("fenced_block")
            continue
        if fenced:
            block.append(line)
            continue
        is_header = any(low.startswith(h) for h in p.header_prefixes) or bool(
            _SMS_HEADER.match(line)
        )
        if is_header:
            after_header = True
            reasons.append("header_line")
        is_quote = bool(p.quote_prefixes) and line.lstrip().startswith(p.quote_prefixes)
        if after_header or is_quote:
            if not after_header:
                reasons.append("quoted_line")
            block.append(line.lstrip("> |").rstrip() if is_quote else line)
            pasted_started = True
            continue
        if block:
            pasted.append("\n".join(block))
            block = []
        flat = squash(line)
        voice = any(m in flat for m in p.voice_markers)
        pushy = any(m in flat for m in p.imperative_markers) and (
            bool(account_candidates(line)) or has_url(line)
        )
        if voice or pushy:
            pasted.append(line.strip())
            reasons.append("third_party_voice")
            certain = False
            pasted_started = True
            continue
        if pasted_started and line.strip():
            user_after_paste = True
        user_lines.append(line)
    if fenced and block:  # an unterminated fence: everything after it is pasted
        pasted.append("\n".join(block))
        block = []
        certain = False
        reasons.append("unterminated_fence")
    if block:
        pasted.append("\n".join(block))
    # Text-embedded structure can be forged by the pasted content itself (a fake closing fence, an
    # unquoted line inside a quoted email), so it is certain only when it is unambiguous: at most one
    # balanced fence pair and nothing of the user's own after the pasted part. The API's separate
    # forwarded box is the only fully certain channel.
    if fence_lines not in (0, 2) or (user_after_paste and not forwarded):
        certain = False
        reasons.append("ambiguous_structure")
        # the structure may be forged by the pasted text itself: nothing is attributed to the user
        user_lines = []
        # the structure may be forged by the pasted text itself: nothing is attributed to the user
        user_lines = []
    pasted = [x[:_MAX_PART] for x in pasted if x.strip()]
    user_text = "\n".join(user_lines).strip()
    if pasted and not user_text:
        certain = False  # nothing of the user's own voice is left to act on
        reasons.append("no_user_instruction")
    single_line_mixed = (
        len(text.splitlines()) <= 1 and not forwarded and "third_party_voice" in reasons
    )
    if single_line_mixed:
        user_text, pasted = text.strip()[:_MAX_PART], [text.strip()[:_MAX_PART]]
        certain = False
        reasons = [*reasons, "single_line_mixed"]
    return MessageSplit(user_text, tuple(pasted), certain, tuple(dict.fromkeys(reasons)))


def looks_third_party(split: MessageSplit) -> bool:
    """True if the message contains pasted content or could not be told apart from it."""
    return split.has_pasted or "single_line_mixed" in split.reasons
