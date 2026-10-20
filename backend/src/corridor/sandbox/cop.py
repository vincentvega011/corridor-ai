"""Simulated Confirmation of Payee (CoP): does the name on a payee match the registered account
holder?

Synthetic registry only (``cop_registry``). The comparison is deterministic and strict enough that
a scam account registered to someone else never matches: names are compared as sets of lower case
words, ignoring legal forms such as "Pty Ltd" (see ``names_match``)."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from corridor.sandbox.models import CopRegistry

_SUFFIXES = frozenset({"pty", "ltd", "limited", "inc", "llc", "co", "company"})  # legal forms only


def _words(name: str) -> frozenset[str]:
    return frozenset(w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in _SUFFIXES)


def names_match(payee_name: str, holder_name: str) -> bool:
    """True when the payee name refers to the registered holder.

    The two word sets (legal forms removed) must be equal, or the payee name must contain every
    word of a holder name of at least two words (the document may be more specific than the
    register). A payee name that is only part of the holder ("Marcus Tan" for "Marcus Tan
    Holdings") never matches: the payee name comes from an untrusted document."""
    a, b = _words(payee_name), _words(holder_name)
    if not a or not b:
        return False
    return a == b or (len(b) >= 2 and b <= a)


def registered_holder(session: Session, account_hash: str) -> str | None:
    """The holder name registered for an account hash, or None when the account is not in the
    registry."""
    return session.scalars(
        select(CopRegistry.holder_name).where(CopRegistry.account_no_hash == account_hash)
    ).first()
