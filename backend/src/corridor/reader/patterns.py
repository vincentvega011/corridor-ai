"""Deterministic pattern scans for injection signals and pasted third party content.

Patterns live in ``data/lang/<code>/injection_patterns.yaml`` and ``pasted_patterns.yaml`` (en, ne and
romanised ne), so adding a language is data only. Matching runs on a squashed form of the text (lower
case, only letters, digits and marks kept) so spacing, punctuation and tiny scanned text do not hide a
phrase. A hit never blocks by itself: it adds caution (R-INJECTION asks for MFA and warns the user).
"""

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from corridor.config import REPO_ROOT

LANG_DIR = REPO_ROOT / "data" / "lang"
INSTRUCTION_CATEGORIES = frozenset(
    {"ignore_previous", "system_tag", "addressed_to_ai", "pay_instruction", "skip_confirm"}
)
_ACCOUNT = re.compile(r"(?<![\w*])(?:\d[ -]?){9,19}(?!\w)")
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)


def squash(text: str) -> str:
    """Lower case text with everything but letters, digits and combining marks removed."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(c for c in folded if unicodedata.category(c)[0] in "LNM")


def words(text: str) -> list[str]:
    """Lower case word tokens (letters, digits and marks)."""
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold())


def account_candidates(text: str) -> list[str]:
    """Digit strings of 9 to 19 digits (spaces and dashes removed), in order of appearance."""
    return ["".join(ch for ch in m.group(0) if ch.isdigit()) for m in _ACCOUNT.finditer(text)]


def has_url(text: str) -> bool:
    return bool(_URL.search(text))


@dataclass(frozen=True)
class Patterns:
    """Compiled pattern data merged over every language folder."""

    phrases: dict[str, tuple[str, ...]]
    tokens: dict[str, frozenset[str]]
    regexes: dict[str, tuple[re.Pattern[str], ...]]
    header_prefixes: tuple[str, ...]
    quote_prefixes: tuple[str, ...]
    voice_markers: tuple[str, ...]
    imperative_markers: tuple[str, ...]

    def scan(self, text: str) -> list[str]:
        """Signal names for ``text``: ``pattern:<category>`` for each category that matched."""
        flat, toks = squash(text), set(words(text))
        hits: list[str] = []
        norm = unicodedata.normalize("NFKC", text)
        for category, patterns in self.regexes.items():
            if any(p.search(norm) for p in patterns):
                hits.append(f"pattern:{category}")
        for category, phrases in self.phrases.items():
            if f"pattern:{category}" in hits:
                continue
            if any(p in flat for p in phrases) or (toks & self.tokens.get(category, frozenset())):
                hits.append(f"pattern:{category}")
        for category, tokens in self.tokens.items():
            if f"pattern:{category}" not in hits and toks & tokens:
                hits.append(f"pattern:{category}")
        return hits


def _load_yaml(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


@lru_cache(maxsize=1)
def load_patterns(lang_dir: Path = LANG_DIR) -> Patterns:
    """Merge the pattern files of every language folder (cached)."""
    phrases: dict[str, list[str]] = {}
    tokens: dict[str, set[str]] = {}
    regexes: dict[str, list[re.Pattern[str]]] = {}
    lists: dict[str, list[str]] = {
        "header_prefixes": [], "quote_prefixes": [], "voice_markers": [], "imperative_markers": [],
    }  # fmt: skip
    for d in sorted(p for p in lang_dir.iterdir() if p.is_dir()):
        inj = _load_yaml(d / "injection_patterns.yaml")
        for cat, items in (inj.get("phrases") or {}).items():  # type: ignore[attr-defined]
            phrases.setdefault(cat, []).extend(squash(str(i)) for i in items)
        for cat, items in (inj.get("regexes") or {}).items():  # type: ignore[attr-defined]
            regexes.setdefault(cat, []).extend(re.compile(str(i), re.IGNORECASE) for i in items)
        for cat, items in (inj.get("tokens") or {}).items():  # type: ignore[attr-defined]
            tokens.setdefault(cat, set()).update(str(i).casefold() for i in items)
        pasted = _load_yaml(d / "pasted_patterns.yaml")
        for key in lists:
            lists[key].extend(str(i) for i in (pasted.get(key) or []))  # type: ignore[attr-defined]
    return Patterns(
        phrases={k: tuple(dict.fromkeys(v)) for k, v in phrases.items()},
        tokens={k: frozenset(v) for k, v in tokens.items()},
        regexes={k: tuple(v) for k, v in regexes.items()},
        header_prefixes=tuple(i.casefold() for i in lists["header_prefixes"]),
        quote_prefixes=tuple(lists["quote_prefixes"]),
        voice_markers=tuple(squash(i) for i in lists["voice_markers"]),
        imperative_markers=tuple(squash(i) for i in lists["imperative_markers"]),
    )
