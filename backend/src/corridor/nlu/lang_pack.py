"""Language packs: all language, number, currency, kinship, calendar and clarify data is loaded
from ``data/lang/<code>/`` and ``data/calendar/`` (docs/spec/modules.md 5.3). No language specific word list
lives in code, so adding Korean or Chinese later is a data folder plus an entry in
``data/lang/packs.yaml``.

Matching is done on *folded* tokens: Latin tokens are lowercased and passed through the ordered
regex rules of ``romanisation_folding.yaml`` (vowel length, doubled consonants, ``z``/``j``), and
Devanagari tokens are NFC normalised with chandrabindu, short and long vowel signs unified. The
same folding is applied to the data and to the user's text, so spelling variants match.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from corridor.config import REPO_ROOT

DATA_DIR = REPO_ROOT / "data"
_DEVA = re.compile(r"[ऀ-ॿ]")
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿"))
_DEVA_FOLD = str.maketrans({"ँ": "ं", "ि": "ी", "ु": "ू", "ऩ": "न"})


def is_devanagari(token: str) -> bool:
    """True if ``token`` contains any Devanagari character."""
    return bool(_DEVA.search(token))


class _Loader(yaml.SafeLoader):
    """YAML 1.2 style booleans: only true and false. Words such as ``on``, ``no`` and ``y`` in the
    language packs are strings (a common YAML 1.1 trap)."""


_Loader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:bool"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$"), list("tf"))


def _load(path: Path) -> dict[str, Any]:
    loaded = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)  # noqa: S506 (safe subclass)
    return loaded if isinstance(loaded, dict) else {}


@dataclass(frozen=True)
class Festival:
    """One festival with its aliases and per year dates (a date may be unverified or missing)."""

    festival_id: str
    names: tuple[tuple[str, ...], ...]  # folded token sequences
    years: dict[int, dict[str, dict[str, Any]]]


@dataclass(frozen=True)
class LangPacks:
    """Merged data of all enabled language packs."""

    enabled: tuple[str, ...]
    folding: tuple[tuple[re.Pattern[str], str], ...]
    number_words: dict[str, int]
    multipliers: dict[str, int]
    ambiguous_alone: frozenset[str]
    suffixes: dict[str, int]
    connectors: frozenset[str]
    currency_words: dict[str, str]
    currency_phrases: tuple[tuple[tuple[str, ...], str], ...]
    currency_ambiguous: dict[str, tuple[str, ...]]
    currency_unsupported: dict[str, str]
    relations: dict[str, str]
    romanised_ne: frozenset[str]
    english_words: frozenset[str]
    loans: frozenset[str]
    non_names: frozenset[str]
    case_suffixes: tuple[str, ...]
    relative_days: dict[str, int]
    relative_periods: tuple[tuple[tuple[str, ...], str], ...]
    weekdays: dict[str, int]
    weekday_next: frozenset[str]
    months: dict[str, int]
    before_markers: tuple[tuple[str, ...], ...]
    for_markers: tuple[tuple[str, ...], ...]
    recurrence: tuple[tuple[tuple[str, ...], str], ...]
    duration_units: frozenset[str]
    condition_markers: frozenset[str]
    reserve_words: dict[str, str]
    cover_triggers: frozenset[str]
    festivals: tuple[Festival, ...]
    intent_keywords: dict[str, tuple[tuple[str, ...], ...]]
    intent_requires: dict[str, tuple[str, ...]]
    attachment_imperatives: tuple[tuple[str, ...], ...]
    imperative_fillers: frozenset[str]
    clarify: dict[str, dict[str, str]]
    replies: dict[str, dict[str, str]]
    policy_reasons: dict[str, dict[str, str]]
    required_slots: dict[str, tuple[str, ...]]
    slot_names: tuple[str, ...]
    meta: dict[str, Any] = field(default_factory=dict)
    name_titles: frozenset[str] = frozenset()
    """Folded titles written before a name ("Mr", "Dr"). Part of the name span, ignored when matching."""
    name_honorifics: frozenset[str] = frozenset()
    """Folded words written after a name that are not part of it ("sir", "madam", "ji", and the
    kinship terms of ``honorific_relations`` such as "dai" and "didi")."""
    honorific_terms: frozenset[str] = frozenset()
    """Folded kinship terms whose relation is in ``honorific_relations`` (used as a suffix after a name)."""
    case_suffixes_latn: tuple[str, ...] = ()
    """Folded romanised case markers ("lai", "ko", ...), also written glued to a name ("Rameshlai")."""
    misspellings: dict[str, str] = field(default_factory=dict)
    """Folded misspelling to the folded correct word, for the keyword classifier only (data/lang intents.yaml)."""
    after_markers: tuple[tuple[str, ...], ...] = ()
    """"after X": not a deadline (no deadline slot is built)."""
    glued_postpositions: tuple[str, ...] = ()
    """Unfolded postpositions that may be written glued to a time word, longest first (data/lang time.yaml)."""
    time_stems: frozenset[str] = frozenset()
    """Folded words a postposition may be split from: relative days, weekdays, months, festival name words."""

    @property
    def name_noise(self) -> frozenset[str]:
        """Words that decorate a name but are not part of it, for matching typed names to stored ones."""
        return self.name_titles | self.name_honorifics | self.honorific_terms

    def relations_ne_latin(self) -> frozenset[str]:
        """Folded Latin kinship words that are Nepali (not English), used for style detection."""
        return frozenset(self.meta.get("ne_relations", ()))

    # ------------------------------------------------------------------ folding
    def fold(self, token: str) -> str:
        """Fold one token for lookup (see module docstring)."""
        t = unicodedata.normalize("NFC", token).translate(_INVISIBLE)
        if is_devanagari(t):
            return t.translate(_DEVA_FOLD)
        t = t.lower()
        for pat, repl in self.folding:
            t = pat.sub(repl, t)
        return t

    def fold_phrase(self, phrase: str) -> tuple[str, ...]:
        """Fold a whitespace separated phrase into a token tuple."""
        return tuple(self.fold(p) for p in phrase.split())

    def strip_case(self, folded: str) -> list[str]:
        """The token itself and, for Devanagari, the token with one case suffix removed."""
        out = [folded]
        if is_devanagari(folded):
            for suf in self.case_suffixes:
                s = suf.translate(_DEVA_FOLD)
                if folded.endswith(s) and len(folded) > len(s):
                    out.append(folded[: -len(s)])
        return out


def _forms(entry: Any) -> list[str]:
    if isinstance(entry, dict):
        return [*(entry.get("deva") or []), *(entry.get("latn") or [])]
    return list(entry or [])


@lru_cache(maxsize=4)
def load_packs(data_dir: Path | None = None) -> LangPacks:
    """Load and merge every enabled language pack (cached)."""
    root = data_dir or DATA_DIR
    meta = _load(root / "lang" / "packs.yaml")
    enabled = tuple(meta["enabled"])
    rules: list[tuple[re.Pattern[str], str]] = []
    for code in enabled:
        for pat, repl in _load(root / "lang" / code / "romanisation_folding.yaml").get("rules", []):
            rules.append((re.compile(pat), repl))
    base = LangPacks.__new__(LangPacks)
    object.__setattr__(base, "folding", tuple(rules))
    fold = base.fold  # folding rules only; other fields are filled below

    number_words: dict[str, int] = {}
    multipliers: dict[str, int] = {}
    ambiguous_alone: set[str] = set()
    suffixes: dict[str, int] = {}
    connectors: set[str] = set()
    cur_words: dict[str, str] = {}
    cur_phrases: list[tuple[tuple[str, ...], str]] = []
    cur_amb: dict[str, tuple[str, ...]] = {}
    cur_unsup: dict[str, str] = {}
    relations: dict[str, str] = {}
    ne_relations: set[str] = set()
    romanised_ne: set[str] = set()
    english: set[str] = set()
    loans: set[str] = set()
    non_names: set[str] = set()
    case_latn: list[str] = []
    titles: set[str] = set()
    honorifics: set[str] = set()
    honorific_rels: set[str] = set()
    case_suffixes: list[str] = []
    rel_days: dict[str, int] = {}
    periods: list[tuple[tuple[str, ...], str]] = []
    weekdays: dict[str, int] = {}
    glued: list[str] = []
    misspell: dict[str, str] = {}
    after: list[tuple[str, ...]] = []
    wk_next: set[str] = set()
    months: dict[str, int] = {}
    before: list[tuple[str, ...]] = []
    for_m: list[tuple[str, ...]] = []
    recurrence: list[tuple[tuple[str, ...], str]] = []
    units: set[str] = set()
    cond_markers: set[str] = set()
    reserve: dict[str, str] = {}
    cover: set[str] = set()
    intents: dict[str, list[tuple[str, ...]]] = {}
    requires: dict[str, tuple[str, ...]] = {}
    imperatives: list[tuple[str, ...]] = []
    fillers: set[str] = set()
    clarify: dict[str, dict[str, str]] = {}
    replies: dict[str, dict[str, str]] = {}
    policy_reasons: dict[str, dict[str, str]] = {}

    for code in enabled:
        d = root / "lang" / code
        n = _load(d / "numbers.yaml")
        for value, forms in (n.get("words") or {}).items():
            for f in _forms(forms):
                number_words[fold(f)] = int(value)
        for value, forms in (n.get("multipliers") or {}).items():
            for f in _forms(forms):
                multipliers[fold(f)] = int(value)
        ambiguous_alone.update(fold(w) for w in n.get("ambiguous_alone") or [])
        suffixes.update({fold(k): int(v) for k, v in (n.get("suffixes") or {}).items()})
        connectors.update(fold(w) for w in n.get("connectors") or [])

        c = _load(d / "currency.yaml")
        for ccy, forms in (c.get("currencies") or {}).items():
            for f in _forms(forms):
                cur_words[fold(f)] = ccy
        for ccy, seqs in (c.get("phrases") or {}).items():
            cur_phrases += [(tuple(fold(w) for w in seq), ccy) for seq in seqs]
        for word, options in (c.get("ambiguous") or {}).items():
            cur_amb[fold(word)] = tuple(options)
        for ccy, words in (c.get("unsupported") or {}).items():
            cur_unsup.update({fold(w): ccy for w in words})

        for rel, forms in (_load(d / "kinship.yaml").get("relations") or {}).items():
            for f in _forms(forms):
                relations[fold(f)] = rel
                if code != "en" and not is_devanagari(f):
                    ne_relations.add(fold(f))

        lex = _load(d / "lexicon.yaml")
        romanised_ne.update(fold(w) for w in lex.get("romanised") or [])
        english.update(fold(w) for w in lex.get("english") or [])
        loans.update(fold(w) for w in lex.get("english_like_loans") or [])
        non_names.update(fold(w) for w in lex.get("non_names") or [])
        case_suffixes += list((lex.get("case_suffixes") or {}).get("deva") or [])
        case_latn += [fold(w) for w in (lex.get("case_suffixes") or {}).get("latn") or []]
        titles.update(fold(w) for w in lex.get("name_titles") or [])
        honorifics.update(fold(w) for w in lex.get("name_honorifics") or [])
        honorific_rels.update(_load(d / "kinship.yaml").get("honorific_relations") or [])

        t = _load(d / "time.yaml")
        for script in (t.get("relative_days") or {}).values():
            rel_days.update({fold(k): int(v) for k, v in script.items()})
        for script in (t.get("relative_periods") or {}).values():
            periods += [(tuple(fold(w) for w in e["tokens"]), e["period"]) for e in script]
        for script in (t.get("weekdays") or {}).values():
            weekdays.update({fold(k): int(v) for k, v in script.items()})
        for script in (t.get("weekday_next_markers") or {}).values():
            wk_next.update(fold(w) for w in script)
        for script in (t.get("months") or {}).values():
            months.update({fold(k): int(v) for k, v in script.items()})
        for script in (t.get("deadline_before_markers") or {}).values():
            before += [tuple(fold(p) for p in w.split()) for w in script]
        for script in (t.get("deadline_after_markers") or {}).values():
            after += [tuple(fold(p) for p in w.split()) for w in script]
        for script in (t.get("glued_postpositions") or {}).values():
            glued += list(script)
        for script in (t.get("deadline_for_markers") or {}).values():
            for_m += [tuple(fold(p) for p in w.split()) for w in script]
        for script in (t.get("recurrence") or {}).values():
            recurrence += [(tuple(fold(w) for w in e["tokens"]), e["freq"]) for e in script]
        for script in (t.get("duration_units") or {}).values():
            units.update(fold(w) for w in script)

        cd = _load(d / "conditions.yaml")
        for script in (cd.get("markers") or {}).values():
            cond_markers.update(fold(w) for w in script)
        for name, forms in (cd.get("reserve_words") or {}).items():
            for f in _forms(forms):
                for part in f.split():
                    reserve[fold(part)] = name
        for script in (cd.get("cover_triggers") or {}).values():
            cover.update(fold(w) for w in script)

        imp = _load(d / "intents.yaml").get("attachment_imperatives") or {}
        for script in ("deva", "latn"):
            imperatives.extend(tuple(fold(p) for p in kw.split()) for kw in imp.get(script) or [])
        fillers.update(fold(w) for w in imp.get("fillers") or [])
        for canon, variants in (_load(d / "intents.yaml").get("misspellings") or {}).items():
            for v in variants:
                misspell[fold(str(v))] = fold(str(canon))
        for name, spec in (_load(d / "intents.yaml").get("intents") or {}).items():
            for script in ("deva", "latn"):
                intents.setdefault(name, []).extend(
                    tuple(fold(p) for p in kw.split()) for kw in spec.get(script) or []
                )
            if spec.get("requires_any_of"):
                requires[name] = tuple(spec["requires_any_of"])

        for slot, styles in (_load(d / "clarify.yaml").get("templates") or {}).items():
            clarify.setdefault(slot, {}).update(styles)
        for key, styles in (_load(d / "replies.yaml").get("templates") or {}).items():
            replies.setdefault(key, {}).update(styles)
        for key, styles in (_load(d / "policy_reasons.yaml").get("reasons") or {}).items():
            policy_reasons.setdefault(key, {}).update(styles)

    slots_cfg = _load(root / "nlu" / "slots.yaml")
    fest_cfg = _load(root / "calendar" / "festivals.yaml")
    festivals: list[Festival] = []
    for fid, spec in (fest_cfg.get("festivals") or {}).items():
        names = tuple(tuple(fold(p) for p in n.split()) for n in _forms(spec["names"]))
        festivals.append(Festival(fid, names, {int(y): v for y, v in spec["years"].items()}))
    stems = {*rel_days, *weekdays, *months, *(w for f in festivals for n in f.names for w in n)}

    return LangPacks(
        enabled=enabled,
        folding=tuple(rules),
        number_words=number_words,
        multipliers=multipliers,
        ambiguous_alone=frozenset(ambiguous_alone),
        suffixes=suffixes,
        connectors=frozenset(connectors),
        currency_words=cur_words,
        currency_phrases=tuple(cur_phrases),
        currency_ambiguous=cur_amb,
        currency_unsupported=cur_unsup,
        relations=relations,
        romanised_ne=frozenset(romanised_ne),
        english_words=frozenset(english),
        loans=frozenset(loans),
        non_names=frozenset(non_names),
        case_suffixes=tuple(dict.fromkeys(case_suffixes)),
        relative_days=rel_days,
        relative_periods=tuple(periods),
        weekdays=weekdays,
        weekday_next=frozenset(wk_next),
        months=months,
        before_markers=tuple(before),
        for_markers=tuple(for_m),
        recurrence=tuple(recurrence),
        duration_units=frozenset(units),
        condition_markers=frozenset(cond_markers),
        reserve_words=reserve,
        cover_triggers=frozenset(cover),
        festivals=tuple(festivals),
        misspellings=dict(misspell),
        after_markers=tuple(dict.fromkeys(after)),
        glued_postpositions=tuple(sorted(dict.fromkeys(glued), key=len, reverse=True)),
        time_stems=frozenset(stems),
        intent_keywords={k: tuple(v) for k, v in intents.items()},
        intent_requires=requires,
        attachment_imperatives=tuple(imperatives),
        imperative_fillers=frozenset(fillers),
        clarify=clarify,
        replies=replies,
        policy_reasons=policy_reasons,
        required_slots={k: tuple(v) for k, v in (slots_cfg.get("required") or {}).items()},
        slot_names=tuple(slots_cfg.get("slot_names") or ()),
        meta={"planned": meta.get("planned", []), "ne_relations": frozenset(ne_relations)},
        name_titles=frozenset(titles),
        name_honorifics=frozenset(honorifics),
        honorific_terms=frozenset(w for w, rel in relations.items() if rel in honorific_rels),
        case_suffixes_latn=tuple(dict.fromkeys(sorted(case_latn, key=len, reverse=True))),
    )


def festival_date(
    fest: Festival, today: date, which: str
) -> tuple[date | None, int | None, dict[str, Any] | None]:
    """The next verified date of ``which`` (``period_start`` or ``main_day``) on or after ``today``.

    Falls back to ``main_day`` when ``period_start`` is not listed. Returns (date, year, entry);
    date is None when no verified date exists, and the caller must ask instead of guessing."""
    best: tuple[date, int, dict[str, Any]] | None = None
    for year, entries in sorted(fest.years.items()):
        entry = entries.get(which) or entries.get("main_day")
        if not entry or not entry.get("verified") or entry.get("date") is None:
            continue
        d = entry["date"]
        if d >= today and (best is None or d < best[0]):
            best = (d, year, entry)
    return (best[0], best[1], best[2]) if best else (None, None, None)
