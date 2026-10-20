"""The customer app's two locale files stay in step, and the Nepali one has no leftover English (P4-1)."""

from __future__ import annotations

import json
import re
from pathlib import Path

MESSAGES = Path(__file__).resolve().parents[3] / "frontend" / "messages"
# proper names, codes and the product name may stay in Latin letters
LATIN_OK = {
    "Corridor",
    "AI",
    "NRB",
    "SANDBOX",
    "QAR",
    "AUD",
    "CNY",
    "NPR",
    "MYR",
    "USD",
    "TOTP",
    "PDF",
}


def load(name: str) -> dict[str, str]:
    return json.loads((MESSAGES / f"{name}.json").read_text(encoding="utf-8"))


def test_every_english_key_exists_in_nepali_and_the_placeholders_match() -> None:
    en, ne = load("en"), load("ne")
    assert sorted(set(en) - set(ne)) == [], "keys missing from ne.json"
    assert sorted(set(ne) - set(en)) == [], "keys in ne.json that en.json lacks"
    for k, v in en.items():
        assert sorted(re.findall(r"\{(\w+)\}", v)) == sorted(re.findall(r"\{(\w+)\}", ne[k])), k
        assert ne[k].strip(), k


def test_nepali_strings_have_no_leftover_english_words() -> None:
    ne = load("ne")
    bad: dict[str, list[str]] = {}
    for k, v in ne.items():
        if k in {"lang.en", "lang.ne"}:
            continue
        words = [
            w for w in re.findall(r"[A-Za-z]{2,}", re.sub(r"\{\w+\}", "", v)) if w not in LATIN_OK
        ]
        if words:
            bad[k] = words
    assert bad == {}, bad


def load_data(name: str) -> object:
    return json.loads((MESSAGES / name).read_text(encoding="utf-8"))


def _ph(s: str) -> list[str]:
    return sorted(re.findall(r"\{(\w+)\}", s))


def test_every_english_key_exists_in_simplified_chinese_and_the_placeholders_match() -> None:
    """A missing zh-CN key fails here (the UI would silently fall back to English)."""
    en, zh = load("en"), load("zh")
    assert sorted(set(en) - set(zh)) == [], "keys missing from zh.json"
    assert sorted(set(zh) - set(en)) == [], "keys in zh.json that en.json lacks"
    for k, v in en.items():
        assert _ph(v) == _ph(zh[k]), k
        assert zh[k].strip(), k


def test_chinese_strings_are_chinese_not_copied_english() -> None:
    zh, en = load("zh"), load("en")
    same_ok = {"app.name", "lang.en", "lang.ne", "lang.zh", "card.val.into"}
    cjk = re.compile(r"[一-鿿]")
    bad = [k for k, v in zh.items() if k not in same_ok and not cjk.search(v) and v == en[k]]
    assert bad == [], bad


def test_the_bank_console_catalogues_match_in_english_and_chinese() -> None:
    en, zh = load("console.en"), load("console.zh")
    assert sorted(set(en) - set(zh)) == [], "keys missing from console.zh.json"
    assert sorted(set(zh) - set(en)) == []
    for k, v in en.items():
        assert _ph(v) == _ph(zh[k]), k


def test_every_server_reply_and_clarification_template_has_a_chinese_version() -> None:
    import yaml

    pairs = {p["id"]: p for p in load_data("server.zh.json")}  # type: ignore[union-attr]
    lang = MESSAGES.parents[1] / "data" / "lang" / "ne"
    for f in ("replies", "clarify"):
        templates = yaml.safe_load((lang / f"{f}.yaml").read_text(encoding="utf-8"))["templates"]
        for key, styles in templates.items():
            p = pairs[f"{f}.{key}"]
            assert p["en"] == styles["en"], key  # the match key is the exact English template
            assert _ph(p["zh"]) == _ph(styles["en"]), key
    assert len(pairs) == sum(
        len(yaml.safe_load((lang / f"{f}.yaml").read_text(encoding="utf-8"))["templates"])
        for f in ("replies", "clarify")
    )
