"""The baseline's direct write tools. The LLM calls these itself; nothing checks them (ADR 0010).

They write only to the baseline database. ``AccountAliases`` exists because the INV-12 prompt guard,
a gateway property kept on for both modes, blocks long digit runs: untrusted text shows an account as
``[ACCT-1 ending 4471]`` and the tools resolve the alias back to the full number the document printed,
so the model can still pay exactly the account a document names."""

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from corridor.contracts.money import CurrencyCode, minor_unit_exponent
from corridor.contracts.tools import ToolSpec
from corridor.sandbox import vault
from corridor.sandbox.models import Account, Payee, Transfer

_RUN = re.compile(r"\d(?:[ \-]?\d){7,}")
_ALIAS = re.compile(r"\[?ACCT-(\d+)(?: ending \d{4})?\]?", re.IGNORECASE)

WRITE_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="transfer",
        description="Send money now from the user's account in that currency to a payee or account.",
        args_schema={
            "type": "object",
            "properties": {
                "payee_or_account": {
                    "type": "string",
                    "description": "saved payee id or name, or an account reference",
                },
                "amount": {
                    "type": "number",
                    "description": "amount in major units, for example 6500.00",
                },
                "currency": {
                    "type": "string",
                    "description": "ISO code: NPR, AUD, QAR, MYR, CNY or USD",
                },
            },
            "required": ["payee_or_account", "amount", "currency"],
        },
    ),
    ToolSpec(
        name="add_payee",
        description="Save a new payee with a display name and an account reference.",
        args_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}, "account": {"type": "string"}},
            "required": ["name", "account"],
        },
    ),
)
WRITE_TOOL_NAMES = frozenset(t.name for t in WRITE_TOOLS)


@dataclass
class AccountAliases:
    """Maps ``ACCT-n`` aliases to the digits printed in untrusted text, per conversation."""

    numbers: dict[int, str] = field(default_factory=dict)

    def alias_text(self, text: str) -> str:
        """Replace every long digit run with an alias (the model never sees the digits)."""

        def sub(m: re.Match[str]) -> str:
            digits = re.sub(r"\D", "", m.group(0))
            for n, d in self.numbers.items():
                if d == digits:
                    return f"[ACCT-{n} ending {digits[-4:]}]"
            n = len(self.numbers) + 1
            self.numbers[n] = digits
            return f"[ACCT-{n} ending {digits[-4:]}]"

        return _RUN.sub(sub, text)

    def resolve(self, ref: str) -> str | None:
        """Digits for an alias or a raw number in a model argument, else None."""
        m = _ALIAS.search(ref)
        if m and int(m.group(1)) in self.numbers:
            return self.numbers[int(m.group(1))]
        digits = re.sub(r"\D", "", ref)
        if len(digits) >= 6:
            return digits
        if (
            len(digits) == 4
        ):  # "2208", "ending 2208", "****2208": the model quotes the last four it was shown
            known = {d for d in self.numbers.values() if d.endswith(digits)}
            if len(known) == 1:
                return next(iter(known))
        return None


class BaselineTools:
    """Executes ``transfer`` and ``add_payee`` against the baseline database, with no checks."""

    def __init__(
        self, engine: Engine, aliases: AccountAliases, secrets_dir: Path | None = None
    ) -> None:
        self.engine, self.aliases, self.secrets_dir = engine, aliases, secrets_dir

    def _payee_by_ref(self, s: Session, user_id: str, ref: str) -> Payee | None:
        payees = list(s.scalars(select(Payee).where(Payee.user_id == user_id)))
        low = ref.strip().lower()
        for p in payees:
            if p.id.lower() == low or p.display_name.lower() == low:
                return p
        digits = self.aliases.resolve(ref)
        if digits:  # an account reference names exactly that account, never a lookalike payee name
            h = vault.account_hash(digits, self.secrets_dir)
            return next((p for p in payees if p.account_no_hash == h), None)
        for p in payees:
            if low and (
                low in p.display_name.lower() or p.display_name.lower().split(" (")[0] in low
            ):
                return p
        return None

    def _new_payee(
        self, s: Session, user_id: str, name: str, digits: str, currency: str, now: datetime
    ) -> Payee:
        p = Payee(
            id="pay_bl_" + hashlib.sha256(f"{user_id}:{digits}".encode()).hexdigest()[:10], user_id=user_id, display_name=name[:120] or "payee",
            region="XX", currency=currency, provider_ref=None, relation=None,
            account_no_masked="****" + digits[-4:], account_no_hash=vault.account_hash(digits, self.secrets_dir),
            verified_at=None, added_via="document", created_at=now,
        )  # fmt: skip
        s.add(p)
        s.flush()
        return p

    def add_payee(self, user_id: str, name: str, account: str, now: datetime) -> dict[str, Any]:
        """Save a payee exactly as the model asked."""
        digits = self.aliases.resolve(account)
        if not digits:
            return {"ok": False, "error": "unknown account reference"}
        with Session(self.engine) as s, s.begin():
            p = self._new_payee(s, user_id, name, digits, "NPR", now)
            return {"ok": True, "payee_id": p.id, "account": p.account_no_masked}

    def transfer(
        self, user_id: str, payee_or_account: str, amount: Any, currency: str, now: datetime
    ) -> dict[str, Any]:
        """Debit the user's account in ``currency`` and record the transfer. No policy, no confirmation."""
        try:
            ccy = CurrencyCode(str(currency).upper())
            minor = int((Decimal(str(amount)) * 10 ** minor_unit_exponent(ccy)).to_integral_value())
        except (ValueError, InvalidOperation):
            return {"ok": False, "error": "invalid amount or currency"}
        if minor <= 0:
            return {"ok": False, "error": "amount must be positive"}
        with Session(self.engine) as s, s.begin():
            acct = s.scalars(
                select(Account)
                .where(
                    Account.user_id == user_id,
                    Account.currency == ccy.value,
                    Account.kind
                    == "spending",  # a deterministic account: row order is undefined without it
                )
                .with_for_update()
            ).first()
            if acct is None:
                return {"ok": False, "error": f"no {ccy.value} account"}
            payee = self._payee_by_ref(s, user_id, payee_or_account)
            if payee is None:
                digits = self.aliases.resolve(payee_or_account)
                if not digits:
                    return {"ok": False, "error": "unknown payee"}
                payee = self._new_payee(
                    s, user_id, f"Account {digits[-4:]}", digits, ccy.value, now
                )
            if acct.balance_minor < minor:
                return {"ok": False, "error": "insufficient funds"}
            acct.balance_minor -= minor
            # deterministic ids (user, payee, amount, how many transfers exist) so a replayed conversation sends the
            # model the same tool results and hits the same cassette entries (P2-6)
            nth = s.scalar(
                select(func.count()).select_from(Transfer).where(Transfer.user_id == user_id)
            )
            tid = (
                "btr_"
                + hashlib.sha256(
                    f"{user_id}:{payee.id}:{minor}:{ccy.value}:{nth}".encode()
                ).hexdigest()[:16]
            )
            s.add(
                Transfer(
                    id=tid, user_id=user_id, from_account=acct.id, payee_id=payee.id, amount_minor=minor,
                    currency=ccy.value, route_json={"type": "baseline"}, status="executed",
                    idempotency_key=f"bl_{uuid.uuid4().hex}", proposal_hash="0" * 64,
                    created_at=now, executed_at=now,
                )
            )  # fmt: skip
            return {
                "ok": True, "transfer_id": tid, "payee_id": payee.id, "payee": payee.display_name,
                "amount_minor": minor, "currency": ccy.value, "balance_after_minor": acct.balance_minor,
            }  # fmt: skip


def now_utc() -> datetime:
    """Wall clock now (the baseline has no injected clock requirement)."""
    return datetime.now(UTC)
