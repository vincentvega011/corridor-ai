"""Route comparison: exactness against brute force, hand computed costs, deadline and limit handling."""

from decimal import Decimal
from itertools import combinations
from pathlib import Path

import pytest
import yaml

from corridor.optimizer.routes import (
    Channel,
    Status,
    cheapest_for_delivery,
    compare,
    enumerate_routes,
    fee_and_delivered,
)

SEEDS = Path(__file__).resolve().parents[4] / "data" / "seeds" / "providers.yaml"
MID = Decimal("104.5")


def channels(pair: str) -> list[Channel]:
    frm, to = pair.split("/")
    rows = yaml.safe_load(SEEDS.read_text(encoding="utf-8"))["providers"]
    return [
        Channel(p["id"], p["name"], p["fixed"], p["pct_bps"], p["margin_bps"], p["min"], p["max"],
                p["daily"], p["eta_hours"])
        for p in rows
        if p["from"] == frm and p["to"] == to
    ]  # fmt: skip


def brute_force_best(chans: list[Channel], send: int, hours: int | None) -> int | None:
    """The cheapest fee over every single channel and every split, point by point (slow, exact)."""
    best: int | None = None
    for c in chans:
        if c.min_send <= send <= c.cap and (hours is None or c.eta_hours <= hours):
            fee, deliv = c.cost(send, MID)
            if deliv > 0 and (best is None or fee < best):
                best = fee
    for a, b in combinations(chans, 2):
        if hours is not None and max(a.eta_hours, b.eta_hours) > hours:
            continue
        for x in range(max(a.min_send, send - b.cap), min(a.cap, send - b.min_send) + 1):
            fa, da = a.cost(x, MID)
            fb, db = b.cost(send - x, MID)
            if da > 0 and db > 0 and (best is None or fa + fb < best):
                best = fa + fb
    return best


def test_fee_arithmetic_by_hand() -> None:
    # BankWire AUD: fixed 2000, 0 bps pct, 80 bps margin, sending 500000 (AUD 5,000.00)
    fee, delivered = fee_and_delivered(500_000, 2000, 0, 80, MID)
    assert fee == 2000 + 3984  # ceil(498000 * 80 / 10000) = 3984
    assert delivered == int((500_000 - fee) * MID)


@pytest.mark.parametrize("pair", ["AUD/NPR", "QAR/NPR", "CNY/NPR"])
@pytest.mark.parametrize("send", [20_000, 120_000, 300_000])
def test_enumeration_matches_brute_force_exactly(pair: str, send: int) -> None:
    chans = channels(pair)
    options = enumerate_routes(chans, MID, send)
    expected = brute_force_best(chans, send, None)
    if expected is None:
        assert options == []
    else:
        assert options[0].fee == expected


@pytest.mark.parametrize("hours", [1, 30, 100])
def test_deadline_filter_matches_brute_force(hours: int) -> None:
    chans = channels("QAR/NPR")
    cmp = compare(chans, MID, 120_000, hours)
    expected = brute_force_best(chans, 120_000, hours)
    if expected is None:
        assert cmp.best is None and cmp.status in (Status.DEADLINE_UNMET, Status.NO_ROUTE)
    else:
        assert cmp.best is not None and cmp.best.fee == expected
        assert all(leg.eta_hours <= hours for leg in cmp.best.legs)


def test_never_dearer_than_any_single_channel_and_savings_are_consistent() -> None:
    chans = channels("AUD/NPR")
    cmp = compare(chans, MID, 500_000, None)
    assert cmp.status is Status.OK and cmp.best is not None and cmp.worst_feasible is not None
    singles = [o for o in cmp.options if len(o.legs) == 1]
    assert cmp.best.fee <= min(o.fee for o in singles)
    assert cmp.savings_minor == cmp.worst_feasible.fee - cmp.best.fee >= 0


def test_wide_split_range_uses_the_edge_window_and_still_matches_brute_force() -> None:
    chans = channels("AUD/NPR")  # a 100,000 wide range for PeakRemit + KathmanduLink at AUD 5,000
    assert enumerate_routes(chans, MID, 500_000)[0].fee == brute_force_best(chans, 500_000, None)


def test_a_split_beats_every_single_channel_for_aud_5000() -> None:
    cmp = compare(channels("AUD/NPR"), MID, 500_000, None)
    assert cmp.best is not None and len(cmp.best.legs) == 2
    assert {leg.name for leg in cmp.best.legs} == {"PeakRemit", "KathmanduLink"}
    single_best = min(o.fee for o in cmp.options if len(o.legs) == 1)
    assert cmp.best.fee < single_best
    assert sum(leg.send for leg in cmp.best.legs) == 500_000  # the legs always add up to the amount


def test_single_leg_mode_never_splits() -> None:
    cmp = compare(channels("AUD/NPR"), MID, 500_000, None, max_legs=1)
    assert cmp.best is not None and len(cmp.best.legs) == 1


def test_no_route_can_meet_a_tight_deadline_and_the_fastest_is_reported() -> None:
    cmp = compare(channels("AUD/NPR"), MID, 300_000, 0)
    assert cmp.status is Status.DEADLINE_UNMET and cmp.best is None
    assert cmp.fastest is not None and cmp.fastest.eta_hours == 2  # SwiftHome


def test_amount_beyond_every_limit_has_no_route() -> None:
    cmp = compare(channels("AUD/NPR"), MID, 99_000_000, None)
    assert cmp.status is Status.NO_ROUTE and cmp.options == ()


def test_daily_limit_blocks_a_channel_that_the_per_transfer_limit_allows() -> None:
    c = Channel("p", "P", 0, 0, 50, 100, 1_000_000, 200_000, 1)
    assert enumerate_routes([c], MID, 300_000) == []
    assert len(enumerate_routes([c], MID, 150_000)) == 1


def test_same_input_same_output() -> None:
    a = compare(channels("CNY/NPR"), MID, 400_000, 48)
    assert a == compare(channels("CNY/NPR"), MID, 400_000, 48)


def test_cheapest_for_delivery_delivers_at_least_the_target_and_is_minimal_per_channel() -> None:
    chans = channels("QAR/NPR")
    target = 5_000_000
    best = cheapest_for_delivery(chans, MID, target)
    assert best is not None and best.delivered >= target
    leg = best.legs[0]
    c = next(c for c in chans if c.provider_id == leg.provider_id)
    if leg.send - 1 >= c.min_send:
        assert c.cost(leg.send - 1, MID)[1] < target  # one unit less would not deliver enough
