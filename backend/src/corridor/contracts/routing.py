"""Route recommendations produced by the optimizer (docs/spec/modules.md 5.11).

Security reason (INV-5, INV-9): the legs of a route are part of what the user confirms, so
they are hashed into the proposal; the explanation facts feed the grounding registry so the
reply can only quote numbers the optimizer actually produced. All money is integer minor
units inside ``Money``; rates are quantised Decimals.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from corridor.contracts.money import Money, Rate
from corridor.contracts.scalars import IdStr
from corridor.contracts.tools import NumericFact


class RouteLeg(BaseModel):
    """One provider leg of a route: how much is sent, the fee, and what arrives."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: IdStr
    send_amount: Money
    fee: Money
    effective_rate: Rate
    delivered: Money
    eta_hours: int = Field(ge=0)


class RouteRecommendation(BaseModel):
    """The optimizer's answer: one or two legs plus cost, rate, ETA and savings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    legs: tuple[RouteLeg, ...] = Field(min_length=1, max_length=2)
    total_cost: Money
    effective_rate: Rate
    delivered: Money
    eta_hours: int = Field(ge=0)
    savings_vs_default: Money
    explanation_facts: tuple[NumericFact, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        send_ccy = {leg.send_amount.currency for leg in self.legs}
        recv_ccy = {leg.delivered.currency for leg in self.legs}
        if len(send_ccy) != 1 or len(recv_ccy) != 1:
            raise ValueError("all legs must share one send currency and one delivered currency")
        if self.total_cost.currency not in send_ccy or self.total_cost.amount_minor < 0:
            raise ValueError("total_cost must be non negative and in the send currency")
        if self.delivered.currency not in recv_ccy:
            raise ValueError("delivered must be in the legs' delivered currency")
        if self.delivered.amount_minor != sum(leg.delivered.amount_minor for leg in self.legs):
            raise ValueError("delivered must equal the sum of the legs' delivered amounts")
        for leg in self.legs:
            if leg.fee.currency not in send_ccy:
                raise ValueError("leg fees must be in the send currency")
        if self.total_cost.amount_minor != sum(leg.fee.amount_minor for leg in self.legs):
            raise ValueError("total_cost must equal the sum of the legs' fees")
        if self.savings_vs_default.currency not in send_ccy:
            raise ValueError("savings_vs_default must be in the send currency")
        if self.eta_hours != max(leg.eta_hours for leg in self.legs):
            raise ValueError("eta_hours must equal the slowest leg's ETA")
        return self
