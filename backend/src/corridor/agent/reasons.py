"""Human text for policy reason keys, in the user's language style (no LLM).

The policy engine returns keys like ``R-TAINT-PAYEE.tainted_payee``; this module looks the text up in
``data/lang/<code>/policy_reasons.yaml``. An unknown key falls back to the key itself, so nothing is
ever hidden from the user or the console.
"""

from collections.abc import Iterable

from corridor.contracts.intent import LanguageTag
from corridor.contracts.money import CurrencyCode
from corridor.formatting import format_local_time, format_minor
from corridor.nlu.clarify import style_for
from corridor.nlu.lang_pack import LangPacks


def render_reasons(
    keys: Iterable[str], language: LanguageTag, deva: bool, packs: LangPacks, tz: str | None = None
) -> list[str]:
    """The text for each reason key in the style of ``language``. Times are shown in ``tz`` (the user's
    time zone) with the zone named; with no zone they are shown in UTC and say so."""
    style = style_for(language, deva)
    out: list[str] = []
    for k in keys:
        base, _, extra = k.partition("@")  # "R-PAYEE-COOLOFF.first_payment_cap@<until>|<cap minor>"
        text = packs.policy_reasons.get(base, {}).get(style, k)
        if extra and base.startswith("R-DUPLICATE"):  # "<time>|<amount minor>|<currency>"
            when, _, rest = extra.partition("|")
            minor, _, ccy = rest.partition("|")
            if minor.isdigit() and ccy in CurrencyCode.__members__:
                text = text.replace("{time}", format_local_time(when, tz, deva=deva)).replace(
                    "{amount}", format_minor(int(minor), CurrencyCode(ccy))
                )
        elif extra:
            until, _, cap = extra.partition("|")
            text = text.replace("{until}", format_local_time(until, tz, deva=deva)).replace(
                "{cap}", format_minor(int(cap), CurrencyCode.NPR) if cap.isdigit() else cap
            )
        out.append(text)
    return out
