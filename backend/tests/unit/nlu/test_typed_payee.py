"""Where a typed payee's account number and name come from decides its provenance (ADR 0018)."""

from corridor.contracts.provenance import Provenance as P
from corridor.nlu.typed_payee import digit_runs, find_account, find_name, least_trusted

H = lambda d: "h:" + d  # noqa: E731


def test_digits_in_the_users_own_words_are_user_typed() -> None:
    a = find_account(
        "add Okafor Property account 062000771234", "", uncertain_split=False, hash_fn=H
    )
    assert a is not None and a.provenance is P.USER_TYPED and a.last4 == "1234"
    assert a.account_hash == "h:062000771234"  # only the hash and the last four leave this module


def test_digits_only_in_pasted_text_are_untrusted() -> None:
    a = find_account("save this payee", "pay 062000771234 today", uncertain_split=False, hash_fn=H)
    assert a is not None and a.provenance is P.UNTRUSTED


def test_an_uncertain_split_makes_every_digit_untrusted() -> None:
    a = find_account("save 062000771234", "x", uncertain_split=True, hash_fn=H)
    assert a is not None and a.provenance is P.UNTRUSTED


def test_digits_a_model_supplied_that_are_in_neither_text_are_model_inferred() -> None:
    a = find_account("save him", "", uncertain_split=False, hash_fn=H, model_digits="062000771234")
    assert a is not None and a.provenance is P.MODEL_INFERRED
    assert find_account("save him", "", uncertain_split=False, hash_fn=H, model_digits="12") is None


def test_two_different_numbers_are_ambiguous_and_none_means_ask() -> None:
    assert find_account("062000771234 and 99887766", "", uncertain_split=False, hash_fn=H) is None
    assert find_account("add my landlord", "", uncertain_split=False, hash_fn=H) is None


def test_separators_and_devanagari_digits_are_read() -> None:
    assert digit_runs("acct 0620-0077-1234") == ["062000771234"]
    assert digit_runs("खाता ०६२००० ७७१२३४") == ["062000771234"]


def test_the_name_comes_from_the_words_around_the_number() -> None:
    assert find_name("Add my new landlord Okafor Property account 062000771234") == (
        "Okafor Property",
        P.USER_TYPED,
    )
    assert find_name("save him", "Bob") == ("Bob", P.MODEL_INFERRED)  # not in the typed words
    assert find_name("save Bob", "Bob") == ("Bob", P.USER_TYPED)
    assert find_name("add account 062000771234") is None


def test_the_saved_row_takes_the_least_trusted_origin() -> None:
    assert least_trusted(P.USER_TYPED, P.UNTRUSTED) is P.UNTRUSTED
    assert least_trusted(P.USER_TYPED, P.MODEL_INFERRED) is P.MODEL_INFERRED
    assert least_trusted(P.USER_TYPED, P.USER_TYPED) is P.USER_TYPED
