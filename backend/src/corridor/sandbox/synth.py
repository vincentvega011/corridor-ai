"""Deterministic synthetic identifiers for the sandbox (no real data anywhere).

Account numbers are derived from a stable key, so the seed, the attack fixtures and the tests all
agree on them without any file having to be shared. They are fictional 10 to 12 digit strings that
never match a real bank format. The numbers are treated as secrets: the database stores only an
HMAC and the last 4 digits, and the full values are registered with the prompt guard.
"""

import hashlib


def account_number(key: str, digits: int = 12) -> str:
    """Return a fictional account number for ``key`` (stable across runs and machines)."""
    h = hashlib.sha256(f"corridor-sandbox-account:{key}".encode()).hexdigest()
    number = "".join(str(int(c, 16) % 10) for c in h)[:digits]
    return "9" + number[1:]  # leading 9 marks it as a sandbox number


def masked(number: str) -> str:
    """Return the masked form shown to users and models: last 4 digits only."""
    return "****" + number[-4:]


def attacker_account_number(key: str, last4: str) -> str:
    """A fictional attacker account number with fixed last 4 digits (for attack fixtures)."""
    return account_number(key)[:-4] + last4
