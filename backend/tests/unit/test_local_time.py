"""Earliest allowed times are shown in the user's time zone with the zone named, in en and ne (P2-7)."""

from corridor.agent.reasons import render_reasons
from corridor.contracts.intent import LanguageTag
from corridor.formatting import format_local_time
from corridor.nlu.lang_pack import load_packs

ISO = "2026-10-12T16:50+00:00"


def test_the_time_is_converted_and_the_zone_is_named() -> None:
    assert (
        format_local_time(ISO, "Australia/Sydney")
        == "2026-10-13 03:50 UTC+11:00 (Australia/Sydney)"
    )
    assert format_local_time(ISO, "Asia/Qatar") == "2026-10-12 19:50 UTC+03:00 (Asia/Qatar)"
    assert format_local_time(ISO, "Asia/Kathmandu") == "2026-10-12 22:35 UTC+05:45 (Asia/Kathmandu)"


def test_no_or_unknown_zone_says_utc_and_devanagari_digits_follow_the_style() -> None:
    assert format_local_time(ISO, None) == "2026-10-12 16:50 UTC+00:00 (UTC)"
    assert format_local_time(ISO, "Not/AZone") == "2026-10-12 16:50 UTC+00:00 (UTC)"
    assert format_local_time(ISO, "Asia/Kathmandu", deva=True).startswith("२०२६-१०-१२ २२:३५")
    assert format_local_time("garbage", "Asia/Kathmandu") == "garbage"


def test_reasons_render_the_earliest_time_in_the_users_zone_in_every_style() -> None:
    packs = load_packs()
    key = f"R-PAYEE-COOLOFF.first_payment_cap_untrusted@{ISO[:-6]}+00:00|0"
    en = render_reasons((key,), LanguageTag.EN, False, packs, "Australia/Sydney")[0]
    assert "2026-10-13 03:50 UTC+11:00 (Australia/Sydney)" in en
    ne = render_reasons((key,), LanguageTag.NE, True, packs, "Asia/Kathmandu")[0]
    assert "२०२६-१०-१२ २२:३५ UTC+०५:४५ (Asia/Kathmandu)" in ne
    lat = render_reasons((key,), LanguageTag.NE_LATN, False, packs, "Asia/Qatar")[0]
    assert "2026-10-12 19:50 UTC+03:00 (Asia/Qatar)" in lat
