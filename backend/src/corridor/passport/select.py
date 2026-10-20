"""Which claim does the user mean, and who is it for? Keyword matching on the user's own
words (a share request is never decided by a model). Anything unclear returns nothing, and the agent
asks: sharing is never guessed (INV-10)."""

import re

KEYWORDS: dict[str, tuple[str, ...]] = {
    "rent_on_time": ("rent", "bhada", "भाडा"),
    "tuition_on_time": ("tuition", "school fee", "semester fee", "university fee", "ट्युसन", "शुल्क"),
    "bills_on_time": ("bill", "bills", "बिल"),
    "money_sent_home": ("remittance", "remit", "send money home", "sends money home", "रेमिट"),
    "no_failed_payments": ("failed", "never missed", "no missed", "never defaulted", "default"),
    "history_months": ("history", "how long", "how many months", "इतिहास"),
}
_AUDIENCE = re.compile(
    r"\b(?:with|to|for)\s+(?:my\s+|a\s+|an\s+|the\s+)?((?:new\s+)?[a-z][a-z ]{2,40})\s*$", re.I
)
DEFAULT_AUDIENCE = "the person who asked"


def claims_from_text(text: str, held: frozenset[str]) -> tuple[str, ...]:
    """Claim ids named in ``text`` that the passport really holds, in a fixed order."""
    low = text.lower()
    found = [cid for cid, words in KEYWORDS.items() if cid in held and any(w in low for w in words)]
    return tuple(found)


def audience_from_text(text: str) -> str:
    """Who the proof is for, from the user's words ("with a new landlord"), plain letters only."""
    m = _AUDIENCE.search(text.strip().rstrip(".!?"))
    if not m:
        return DEFAULT_AUDIENCE
    name = re.sub(r"\s+", " ", m.group(1)).strip().lower()
    return name[:60] or DEFAULT_AUDIENCE
