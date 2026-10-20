"""Concurrency and failure injection for the executor (docs/spec/modules.md 5.9 acceptance)."""

import random
import threading
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from corridor.contracts.confirm import ConfirmationLevel as L
from corridor.contracts.ledger import LedgerEventType
from corridor.executor import ExecutionRejected, Executor
from corridor.ledger import Ledger, LedgerError, verify_chain
from executor_helpers import CFG, balance, count, grant_for, pay_landlord, run

START = "SELECT balance_minor FROM accounts WHERE id = 'acc_ramesh_qar'"


def in_threads(n: int, fn: Any) -> list[Any]:
    out: list[Any] = [None] * n

    def work(i: int) -> None:
        try:
            out[i] = fn(i)
        except ExecutionRejected as exc:
            out[i] = exc

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return out


def chain_valid(engine: Engine) -> bool:
    with Session(engine) as s:
        return verify_chain(s).valid


def test_twenty_parallel_executions_with_one_key_make_exactly_one_transfer(sandbox: Engine) -> None:
    ex = Executor(sandbox, CFG)
    p = pay_landlord(5_000, key="idem-parallel")
    g = grant_for(sandbox, p)
    start = balance(sandbox, "acc_ramesh_qar")
    results = in_threads(20, lambda i: run(ex, p, g))
    ok = [r for r in results if not isinstance(r, Exception)]
    assert len(ok) == 20  # every caller gets the one result
    assert sum(not r.replayed for r in ok) == 1
    assert {r.transfer_id for r in ok} == {ok[0].transfer_id}
    assert count(sandbox, "transfers", "idempotency_key = 'idem-parallel'") == 1
    assert balance(sandbox, "acc_ramesh_qar") == start - 5_000
    assert count(sandbox, "ledger_entries", "event_type = 'execution'") == 1
    assert chain_valid(sandbox)


def test_parallel_payments_never_overdraw_the_account(sandbox: Engine) -> None:
    with sandbox.begin() as c:
        c.execute(text("UPDATE accounts SET balance_minor = 100000 WHERE id = 'acc_ramesh_qar'"))
    ex = Executor(sandbox, CFG)
    proposals = [pay_landlord(30_000) for _ in range(12)]  # QAR 300 each, QAR 1,000 available
    grants = [grant_for(sandbox, p, L.MFA) for p in proposals]
    results = in_threads(12, lambda i: run(ex, proposals[i], grants[i]))
    done = [r for r in results if not isinstance(r, Exception)]
    assert 1 <= len(done) <= 3
    final = balance(sandbox, "acc_ramesh_qar")
    assert final >= 0 and final == 100_000 - 30_000 * len(done)
    assert count(sandbox, "transfers", "idempotency_key not like 'seed-%'") == len(done)
    assert count(sandbox, "ledger_entries", "event_type = 'execution'") == len(done)
    assert chain_valid(sandbox)


class RandomlyFailingLedger(Ledger):
    """Fails 40% of execution entries, to prove transfers and ledger stay in step."""

    def __init__(self, seed: int) -> None:
        self.rng, self.lock = random.Random(seed), threading.Lock()

    def append(self, session, **kw):  # type: ignore[no-untyped-def]
        if kw["event_type"] is LedgerEventType.EXECUTION:
            with self.lock:
                fail = self.rng.random() < 0.4
            if fail:
                raise LedgerError("injected")
        return super().append(session, **kw)


def test_random_failure_injection_keeps_ledger_and_transfers_consistent(sandbox: Engine) -> None:
    with sandbox.begin() as c:
        c.execute(text("UPDATE accounts SET balance_minor = 10000000 WHERE id = 'acc_ramesh_qar'"))
    start = balance(sandbox, "acc_ramesh_qar")
    ex = Executor(sandbox, CFG, RandomlyFailingLedger(seed=7))
    proposals = [pay_landlord(1_000 + i) for i in range(6)]  # stays under the velocity limit
    grants = [grant_for(sandbox, p) for p in proposals]
    results = in_threads(6, lambda i: run(ex, proposals[i], grants[i]))
    done = [r for r in results if not isinstance(r, Exception)]
    failed = [r for r in results if isinstance(r, ExecutionRejected)]
    assert len(done) + len(failed) == 6 and failed and done  # the injection hit some, not all
    assert balance(sandbox, "acc_ramesh_qar") == start - sum(r.amount_minor for r in done)
    with sandbox.connect() as c:
        ids = {
            r[0]
            for r in c.execute(
                text("SELECT id FROM transfers WHERE idempotency_key NOT LIKE 'seed-%'")
            )
        }
        ledgered = {
            __import__("json").loads(r[0])["transfer_id"]
            for r in c.execute(
                text(
                    "SELECT payload_canonical_json FROM ledger_entries WHERE event_type = 'execution'"
                )
            )
        }
    assert ids == ledgered == {r.transfer_id for r in done}
    assert chain_valid(sandbox)


def test_parallel_payments_from_two_accounts_cannot_beat_the_daily_limit(sandbox: Engine) -> None:
    """The per user lock makes the daily total (all accounts) one decision at a time."""
    with sandbox.begin() as c:
        c.execute(text("UPDATE accounts SET balance_minor = 2000000 WHERE id = 'acc_priya_aud'"))
    ex = Executor(sandbox, CFG)
    aud = pay_landlord(
        800_000, user="usr_priya", account="acc_priya_aud", payee="pay_priya_landlord",
        currency=__import__("corridor.contracts", fromlist=["x"]).CurrencyCode.AUD,
    )  # fmt: skip  # AUD 8,000 is about NPR 700,000
    npr = pay_landlord(
        60_000_000, user="usr_priya", account="acc_priya_npr", payee="pay_priya_parents",
        currency=__import__("corridor.contracts", fromlist=["x"]).CurrencyCode.NPR,
    )  # fmt: skip  # NPR 600,000: each is under NPR 1,000,000, together they are not
    grants = [grant_for(sandbox, aud, L.MFA), grant_for(sandbox, npr, L.MFA)]
    results = in_threads(2, lambda i: run(ex, (aud, npr)[i], grants[i]))
    done = [r for r in results if not isinstance(r, Exception)]
    denied = [r for r in results if isinstance(r, ExecutionRejected)]
    assert len(done) == 1 and len(denied) == 1
    assert denied[0].reason == "policy_denied" and "R-DAILY-LIMIT" in denied[0].rule_ids
    assert chain_valid(sandbox)
