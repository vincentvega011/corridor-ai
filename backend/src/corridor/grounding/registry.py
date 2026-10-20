"""The per-turn registry of facts a reply may state (docs/spec/modules.md 5.14, INV-9).

Every tool result registers its numeric facts here, and so do the user's own typed values (marked as
user facts). Nothing else can make a number acceptable: a model cannot add to the registry.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from corridor.contracts.money import CurrencyCode, minor_unit_exponent
from corridor.contracts.tools import FactKind, NumericFact, ToolResult


def sig4(value: Decimal) -> Decimal:
    """A value rounded to 4 significant digits (how rates are compared)."""
    if value == 0:
        return Decimal(0)
    return Decimal(f"{value:.4g}")


@dataclass
class ToolResultRegistry:
    """Money, rates, counts, basis points and dates that are allowed in the reply."""

    money: set[tuple[int, str]] = field(default_factory=set)
    rates: set[Decimal] = field(default_factory=set)
    counts: set[int] = field(default_factory=set)
    bps: set[int] = field(default_factory=set)
    dates: set[date] = field(default_factory=set)
    user_facts: int = 0
    tool_facts: int = 0

    def add_fact(self, fact: NumericFact) -> None:
        self.tool_facts += 1
        if fact.kind is FactKind.MONEY and fact.amount_minor is not None and fact.currency:
            self.money.add((fact.amount_minor, fact.currency.value))
        elif fact.kind is FactKind.RATE and fact.rate is not None:
            self.rates.add(sig4(fact.rate))
        elif fact.kind is FactKind.COUNT and fact.count is not None:
            self.counts.add(fact.count)
            if "bps" in fact.label:
                self.bps.add(fact.count)
        elif fact.kind is FactKind.DATE and fact.on is not None:
            self.dates.add(fact.on)

    def add_tool_result(self, result: ToolResult) -> None:
        for fact in result.facts:
            self.add_fact(fact)

    def add_user_money(self, minor: int, currency: CurrencyCode | str) -> None:
        self.user_facts += 1
        self.money.add((minor, CurrencyCode(currency).value))

    def add_user_date(self, on: date) -> None:
        self.user_facts += 1
        self.dates.add(on)

    def add_money(self, minor: int, currency: CurrencyCode | str) -> None:
        """A figure the system computed from registered facts (for example a receipt)."""
        self.tool_facts += 1
        self.money.add((minor, CurrencyCode(currency).value))

    def majors(self) -> set[Decimal]:
        """The registered money amounts in major units (for bare numbers without a currency)."""
        return {Decimal(m) / (10 ** minor_unit_exponent(CurrencyCode(c))) for m, c in self.money}
