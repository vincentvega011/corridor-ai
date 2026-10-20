"""The hash-chained audit ledger: appends, verification, tamper detection, role limits, Merkle."""

import threading
from datetime import datetime
from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session

from corridor.contracts.ledger import GENESIS_PREV_HASH, LedgerActor, LedgerEventType
from corridor.ledger import (
    Ledger,
    LedgerError,
    compute_roots,
    inclusion_proof,
    merkle_root,
    proof_for,
    verify_chain,
    verify_inclusion,
)
from corridor.ledger.chain import _row_to_hashes
from ledger_helpers import PVH, T0, admin_sql, append_n


def verify(engine: Engine):  # type: ignore[no-untyped-def]
    with Session(engine) as s:
        return verify_chain(s)


def test_empty_ledger_is_valid(sandbox: Engine) -> None:
    r = verify(sandbox)
    assert r.valid and r.entries_checked == 0


def test_chain_starts_at_genesis_and_verifies(sandbox: Engine) -> None:
    append_n(sandbox, 5)
    with Session(sandbox) as s:
        rows = s.execute(
            text("SELECT seq, prev_hash, entry_hash FROM ledger_entries ORDER BY seq")
        ).all()
    assert [r.seq for r in rows] == [1, 2, 3, 4, 5]
    assert rows[0].prev_hash == GENESIS_PREV_HASH
    assert all(rows[i].prev_hash == rows[i - 1].entry_hash for i in range(1, 5))
    r = verify(sandbox)
    assert r.valid and r.entries_checked == 5


@pytest.mark.parametrize(
    ("column", "value"),
    [
        (
            "payload_canonical_json",
            '{"i":2,"amount_minor":999999,"session_id":"s1","user_id":"usr_ramesh"}',
        ),
        ("actor", "user"),
        ("event_type", "execution"),
        ("trace_id", "forged"),
        ("policy_version_hash", "e" * 64),
        ("ts", "2030-01-01T00:00:00+00:00"),
        ("prev_hash", "f" * 64),
        ("entry_hash", "f" * 64),
        ("payload_hash", "f" * 64),
    ],
)
def test_a_change_to_any_single_column_is_detected(
    sandbox: Engine, column: str, value: str
) -> None:
    append_n(sandbox, 5)
    expr = "CAST(:v AS timestamptz)" if column == "ts" else ":v"
    admin_sql(sandbox, f"UPDATE ledger_entries SET {column} = {expr} WHERE seq = 3", {"v": value})
    r = verify(sandbox)
    assert not r.valid and r.first_broken_seq == 3


def test_a_non_canonical_payload_is_detected(sandbox: Engine) -> None:
    append_n(sandbox, 3)
    admin_sql(
        sandbox,
        "UPDATE ledger_entries SET payload_canonical_json = payload_canonical_json || ' ' WHERE seq = 2",
    )
    r = verify(sandbox)
    assert not r.valid and r.first_broken_seq == 2


def test_deletion_in_the_middle_is_detected(sandbox: Engine) -> None:
    append_n(sandbox, 5)
    admin_sql(sandbox, "DELETE FROM ledger_entries WHERE seq = 3")
    r = verify(sandbox)
    assert not r.valid and r.first_broken_seq == 3 and "gap" in (r.reason or "")


def test_deleting_the_newest_entries_is_detected_once_a_root_exists(sandbox: Engine) -> None:
    append_n(sandbox, 4)
    with Session(sandbox) as s, s.begin():
        assert compute_roots(s, T0) > 0
    assert verify(sandbox).valid
    admin_sql(sandbox, "DELETE FROM ledger_entries WHERE seq = 4")
    r = verify(sandbox)
    assert not r.valid and "Merkle" in (r.reason or "")


def test_insertion_of_a_forged_row_is_detected(sandbox: Engine) -> None:
    append_n(sandbox, 3)
    admin_sql(
        sandbox,
        "INSERT INTO ledger_entries VALUES (4, now(), 'x', 'agent', 'proposal', '{}', :h, :h, :h, :h)",
        {"h": "a" * 64},
    )
    r = verify(sandbox)
    assert not r.valid and r.first_broken_seq == 4


def test_reordering_is_detected(sandbox: Engine) -> None:
    append_n(sandbox, 4)
    admin_sql(sandbox, "UPDATE ledger_entries SET seq = 1000 WHERE seq = 2")
    admin_sql(sandbox, "UPDATE ledger_entries SET seq = 2 WHERE seq = 3")
    admin_sql(sandbox, "UPDATE ledger_entries SET seq = 3 WHERE seq = 1000")
    r = verify(sandbox)
    assert not r.valid and r.first_broken_seq in (2, 3)


def test_a_forged_genesis_is_detected(sandbox: Engine) -> None:
    append_n(sandbox, 3)
    admin_sql(sandbox, "UPDATE ledger_entries SET prev_hash = :h WHERE seq = 1", {"h": "1" * 64})
    r = verify(sandbox)
    assert not r.valid and r.first_broken_seq == 1


def test_rewriting_history_consistently_is_caught_by_the_stored_roots(sandbox: Engine) -> None:
    """Someone who recomputes the whole chain still cannot match a root stored earlier."""
    from corridor.contracts.ledger import LedgerActor as A
    from corridor.contracts.ledger import LedgerEventType as E
    from corridor.contracts.ledger import compute_entry_hash, payload_hash_of

    append_n(sandbox, 4)
    with Session(sandbox) as s, s.begin():
        compute_roots(s, T0)
    with Session(sandbox) as s:
        rows = s.execute(text("SELECT * FROM ledger_entries ORDER BY seq")).all()
    prev = GENESIS_PREV_HASH
    for r in rows:
        payload = (
            r.payload_canonical_json.replace('"amount_minor":1001', '"amount_minor":5')
            if r.seq == 2
            else r.payload_canonical_json
        )
        ph = payload_hash_of(payload)
        eh = compute_entry_hash(
            prev, r.seq, r.ts, r.trace_id, A(r.actor), E(r.event_type), ph, r.policy_version_hash
        )
        admin_sql(
            sandbox,
            "UPDATE ledger_entries SET payload_canonical_json = :p, payload_hash = :ph, "
            "prev_hash = :pv, entry_hash = :eh WHERE seq = :s",
            {"p": payload, "ph": ph, "pv": prev, "eh": eh, "s": r.seq},
        )
        prev = eh
    r = verify(sandbox)
    assert not r.valid and "Merkle" in (r.reason or "")


def test_naive_timestamps_are_reported_as_broken_not_a_crash() -> None:
    row = SimpleNamespace(
        ts=datetime(2026, 1, 1), payload_canonical_json="{}", prev_hash="0" * 64, seq=1,
        trace_id="t", actor="agent", event_type="proposal", payload_hash="0" * 64,
        policy_version_hash="0" * 64,
    )  # fmt: skip
    with pytest.raises(ValueError, match="naive"):
        _row_to_hashes(row)


def test_concurrent_appends_keep_the_chain_contiguous_and_valid(sandbox: Engine) -> None:
    errors: list[Exception] = []

    def work(k: int) -> None:
        try:
            append_n(sandbox, 5, session_id=f"s{k}")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(k,)) for k in range(16)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    r = verify(sandbox)
    assert r.valid and r.entries_checked == 80
    with Session(sandbox) as s:
        seqs = [x[0] for x in s.execute(text("SELECT seq FROM ledger_entries ORDER BY seq"))]
    assert seqs == list(range(1, 81))


def test_the_app_role_cannot_update_delete_or_truncate(sandbox: Engine) -> None:
    append_n(sandbox, 2)
    for sql in (
        "UPDATE ledger_entries SET trace_id = 'x'",
        "DELETE FROM ledger_entries",
        "TRUNCATE ledger_entries",
        "UPDATE ledger_roots SET root_hash = 'x'",
    ):
        with Session(sandbox) as s, pytest.raises(ProgrammingError), s.begin():
            s.execute(text("SET LOCAL ROLE corridor_ledger"))
            s.execute(text(sql))
    with Session(sandbox) as s, s.begin():  # but it can insert and read
        s.execute(text("SET LOCAL ROLE corridor_ledger"))
        assert s.execute(text("SELECT count(*) FROM ledger_entries")).scalar_one() == 2


def test_even_the_table_owner_is_stopped_by_the_append_only_trigger(sandbox: Engine) -> None:
    append_n(sandbox, 1)
    with Session(sandbox) as s, pytest.raises(DBAPIError, match="append-only"), s.begin():
        s.execute(text("UPDATE ledger_entries SET trace_id = 'x'"))


def test_failed_appends_raise_ledger_error(sandbox: Engine) -> None:
    with Session(sandbox) as s, pytest.raises(LedgerError), s.begin():
        Ledger().append(
            s, trace_id="t", actor=LedgerActor.AGENT, event_type=LedgerEventType.PROPOSAL,
            user_id="u", session_id=None, data={"price": 1.5}, policy_version_hash=PVH, now=T0,
        )  # fmt: skip
    with Session(sandbox) as s, pytest.raises(LedgerError), s.begin():
        Ledger().append(
            s, trace_id="t", actor=LedgerActor.AGENT, event_type=LedgerEventType.PROPOSAL,
            user_id="u", session_id=None, data={}, policy_version_hash="bad", now=T0,
        )  # fmt: skip


def test_an_entry_rolls_back_with_its_transaction(sandbox: Engine) -> None:
    with Session(sandbox) as s:
        s.begin()
        Ledger().append(
            s, trace_id="t", actor=LedgerActor.SYSTEM, event_type=LedgerEventType.EXECUTION,
            user_id="u", session_id=None, data={"x": 1}, policy_version_hash=PVH, now=T0,
        )  # fmt: skip
        s.rollback()
    assert verify(sandbox).entries_checked == 0


def test_roots_and_inclusion_proofs(sandbox: Engine) -> None:
    append_n(sandbox, 5, session_id="sA")
    append_n(sandbox, 3, session_id="sB")
    with Session(sandbox) as s, s.begin():
        written = compute_roots(s, T0)
        assert written == 3  # one day and two sessions
        assert compute_roots(s, T0) == 0  # unchanged roots are not stored twice
    with Session(sandbox) as s:
        p = proof_for(s, 4)
        assert p is not None and p["valid"] and p["scope"] == "day" and p["leaf_count"] == 8
        assert verify_inclusion(
            p["entry_hash"], [(x["side"], x["hash"]) for x in p["proof"]], p["root"]
        )
        assert not verify_inclusion(
            "0" * 64, [(x["side"], x["hash"]) for x in p["proof"]], p["root"]
        )
        assert proof_for(s, 999) is None
    append_n(
        sandbox, 1, session_id="sA"
    )  # more entries: a new snapshot, the old one still verifies
    with Session(sandbox) as s, s.begin():
        assert compute_roots(s, T0) >= 1
    assert verify(sandbox).valid


@given(st.lists(st.binary(min_size=1, max_size=8), min_size=1, max_size=40, unique=True), st.data())
def test_merkle_proofs_verify_for_every_leaf_and_fail_for_a_wrong_one(
    leaves: list[bytes], data: st.DataObject
) -> None:
    import hashlib

    hashes = [hashlib.sha256(b).hexdigest() for b in leaves]
    root = merkle_root(hashes)
    i = data.draw(st.integers(0, len(hashes) - 1))
    proof = inclusion_proof(hashes, i)
    assert verify_inclusion(hashes[i], proof, root)
    assert not verify_inclusion(hashlib.sha256(b"other").hexdigest(), proof, root)
    assert not verify_inclusion(hashes[i], proof, "0" * 64)


def test_merkle_edge_cases() -> None:
    assert merkle_root([]) == merkle_root([])
    with pytest.raises(IndexError):
        inclusion_proof(["a" * 64], 1)
    assert verify_inclusion("a" * 64, [("up", "b" * 64)], "c" * 64) is False
    assert verify_inclusion("zz", [], "c" * 64) is False
    assert merkle_root(["a" * 64]) != merkle_root(["b" * 64])


def test_reserved_payload_keys_cannot_be_overridden_by_the_caller(sandbox: Engine) -> None:
    import json

    with Session(sandbox) as s, s.begin():
        e = Ledger().append(
            s, trace_id="t", actor=LedgerActor.AGENT, event_type=LedgerEventType.PROPOSAL,
            user_id="usr_ramesh", session_id="s1", data={"user_id": "usr_priya", "session_id": "x"},
            policy_version_hash=PVH, now=T0,
        )  # fmt: skip
    payload = json.loads(e.payload_canonical_json)
    assert payload["user_id"] == "usr_ramesh" and payload["session_id"] == "s1"
