"""Tuition funding plan with OR-Tools CP-SAT (P3-1, ADR 0014).

Problem: pay a fee of ``amount`` (target currency minor units) before a due date, drawing on the
user's own accounts and on money asked from family members, at the lowest total cost. Constraints:
the full fee is funded, each own account gives at most its balance (the spending account keeps a
minimum buffer), each family request stays within the link limit, and a family channel obeys its
minimum, per transfer and daily limit and arrives before the due date.

The model is exact and integer: channel fees are the same ceiling and floor arithmetic as
``corridor.optimizer.routes`` (a test proves the two agree), written as linear constraints with
auxiliary integer variables. Solving is deterministic: seed 0, one worker, a fixed time limit, and
four lexicographic stages: lowest total fees (rounded up to whole minor units, so sub-cent rounding
noise cannot pick a plan), then the least money asked from family, then the least use of savings,
then the fewest family sources. Only a proven OPTIMAL solution is returned: a timeout or any solver
problem gives "no plan" and never a partial plan (INV-10). OR-Tools is a local library, there is no
network call. The planner only returns numbers; paying and asking are separate ActionProposals the
user confirms one by one (INV-1, INV-5).
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from ortools.sat.python import cp_model

from corridor.optimizer.routes import Channel, ceil_div

MID_SCALE = 10**8  # the mid rate is carried as an integer over this scale (rates have 8 decimals)
TIME_LIMIT_S = 5.0


class PlanStatus(StrEnum):
    OK = "ok"
    INFEASIBLE = "infeasible"  # no allocation funds the fee in time
    NO_PLAN = "no_plan"  # the solver did not prove an optimum (fail closed)


@dataclass(frozen=True)
class OwnSource:
    """An account of the user in the fee's currency; ``keep_minor`` stays behind (buffer)."""

    source_id: str
    balance_minor: int
    kind: str  # spending | savings
    keep_minor: int = 0

    @property
    def available(self) -> int:
        return max(self.balance_minor - self.keep_minor, 0)


@dataclass(frozen=True)
class FamilySource:
    """A family member who may be asked. ``limit_minor`` is in the contributor's currency and covers
    the sum over channels; ``mid`` converts contributor currency to the fee currency."""

    source_id: str
    limit_minor: int
    mid: Decimal
    channels: tuple[Channel, ...]


@dataclass(frozen=True)
class Problem:
    amount_minor: int
    hours_until_due: int
    own: tuple[OwnSource, ...]
    family: tuple[FamilySource, ...] = ()


@dataclass(frozen=True)
class Contribution:
    """How much one source gives. For family money, ``send_minor`` is in the contributor's currency
    and ``amount_minor`` is what arrives in the fee's currency."""

    source_id: str
    kind: str  # spending | savings | family
    amount_minor: int
    send_minor: int = 0
    provider_id: str | None = None
    fee_minor: int = 0
    eta_hours: int = 0


@dataclass(frozen=True)
class Plan:
    status: PlanStatus
    contributions: tuple[Contribution, ...] = ()
    cost_minor: int = 0  # total fees, in the fee currency's minor units, rounded up
    family_received: int = 0
    savings_used: int = 0
    family_sources: int = 0
    pay_from_own: dict[str, int] = field(default_factory=dict)

    @property
    def total_received(self) -> int:
        return sum(c.amount_minor for c in self.contributions)


def mid_num(mid: Decimal) -> int:
    return int((mid * MID_SCALE).to_integral_value())


def _model(p: Problem) -> tuple[cp_model.CpModel, dict[str, Any]]:
    m = cp_model.CpModel()
    xs: dict[str, cp_model.IntVar] = {}
    for o in p.own:
        xs[o.source_id] = m.new_int_var(0, min(o.available, p.amount_minor), f"own_{o.source_id}")
    recv_terms: list[cp_model.IntVar] = []
    cost_terms: list[cp_model.LinearExpr] = []
    fam_used: list[cp_model.IntVar] = []
    chan: list[
        tuple[
            FamilySource,
            Channel,
            cp_model.IntVar,
            cp_model.IntVar,
            cp_model.IntVar,
            cp_model.IntVar,
        ]
    ] = []
    for f in p.family:
        sends = []
        for c in f.channels:
            if c.eta_hours > p.hours_until_due:
                continue
            cap = min(c.cap, f.limit_minor)
            if cap < c.min_send:
                continue
            y = m.new_bool_var(f"y_{f.source_id}_{c.provider_id}")
            send = m.new_int_var(0, cap, f"send_{f.source_id}_{c.provider_id}")
            m.add(send >= c.min_send).only_enforce_if(y)
            m.add(send == 0).only_enforce_if(y.Not())
            pct = m.new_int_var(0, cap, "pct")
            # pct = ceil(send * pct_bps / 10000)
            m.add(10_000 * pct >= send * c.pct_bps)
            m.add(10_000 * pct < send * c.pct_bps + 10_000).only_enforce_if(y)
            m.add(pct == 0).only_enforce_if(y.Not())
            after = m.new_int_var(0, cap, "after")  # max(send - fixed - pct, 0)
            m.add(after == send - c.fixed - pct).only_enforce_if(y)
            m.add(after == 0).only_enforce_if(y.Not())
            marg = m.new_int_var(0, cap, "marg")  # ceil(after * margin_bps / 10000)
            m.add(10_000 * marg >= after * c.margin_bps)
            m.add(10_000 * marg < after * c.margin_bps + 10_000).only_enforce_if(y)
            m.add(marg == 0).only_enforce_if(y.Not())
            fee = m.new_int_var(0, cap, "fee")
            m.add(fee == c.fixed * y + pct + marg)
            net = m.new_int_var(0, cap, "net")
            m.add(net == send - fee)
            mn = mid_num(f.mid)
            recv = m.new_int_var(0, cap * mn // MID_SCALE + 1, "recv")
            # recv = floor(net * mid)
            m.add(MID_SCALE * recv <= net * mn)
            m.add(MID_SCALE * recv > net * mn - MID_SCALE).only_enforce_if(y)
            m.add(recv == 0).only_enforce_if(y.Not())
            sends.append(send)
            recv_terms.append(recv)
            cost_terms.append(send * mn - recv * MID_SCALE)
            chan.append((f, c, y, send, fee, recv))
        if sends:
            m.add(sum(sends) <= f.limit_minor)
            used = m.new_bool_var(f"fam_{f.source_id}")
            m.add(sum(sends) >= 1).only_enforce_if(used)
            m.add(sum(sends) == 0).only_enforce_if(used.Not())
            fam_used.append(used)
    own_total = sum(xs.values()) if xs else 0
    recv_total = sum(recv_terms) if recv_terms else 0
    m.add(own_total + recv_total >= p.amount_minor)
    m.add(own_total <= p.amount_minor)
    cost_units = m.new_int_var(0, 10**9, "cost_units")  # ceil(total fees): fees are never negative
    m.add(cost_units * MID_SCALE >= (sum(cost_terms) if cost_terms else 0))
    cost = cost_units
    fam_recv = sum(recv_terms) if recv_terms else 0
    savings = sum(xs[o.source_id] for o in p.own if o.kind == "savings") if p.own else 0
    return m, {
        "xs": xs,
        "chan": chan,
        "cost": cost,
        "fam_recv": fam_recv,
        "savings": savings,
        "fam": sum(fam_used) if fam_used else 0,
    }


def _solve(m: cp_model.CpModel) -> tuple[cp_model.CpSolver, cp_model.CpSolverStatus]:
    s = cp_model.CpSolver()
    s.parameters.random_seed = 0
    s.parameters.num_workers = 1
    s.parameters.max_time_in_seconds = TIME_LIMIT_S
    return s, s.solve(m)


def solve(p: Problem) -> Plan:
    """The cheapest plan, or INFEASIBLE, or NO_PLAN. Never raises, never returns a partial plan."""
    try:
        if p.amount_minor <= 0 or p.hours_until_due < 0:
            return Plan(PlanStatus.NO_PLAN)
        m, v = _model(p)
        cost, fam_recv, savings, fam = v["cost"], v["fam_recv"], v["savings"], v["fam"]
        s, st = _solve(m)
        if st == cp_model.INFEASIBLE:
            return Plan(PlanStatus.INFEASIBLE)
        # stage 1: lowest total fees
        m.minimize(cost)
        s, st = _solve(m)
        if st != cp_model.OPTIMAL:
            return Plan(PlanStatus.NO_PLAN)
        best_cost = int(s.objective_value)
        m.add(cost <= best_cost)
        # stage 2: least money asked from family
        m.minimize(fam_recv)
        s, st = _solve(m)
        if st != cp_model.OPTIMAL:
            return Plan(PlanStatus.NO_PLAN)
        m.add(fam_recv <= int(s.objective_value))
        # stage 3: least use of savings
        m.minimize(savings)
        s, st = _solve(m)
        if st != cp_model.OPTIMAL:
            return Plan(PlanStatus.NO_PLAN)
        m.add(savings <= int(s.objective_value))
        # stage 4: fewest family sources asked
        m.minimize(fam)
        s, st = _solve(m)
        if st != cp_model.OPTIMAL:
            return Plan(PlanStatus.NO_PLAN)
        return _read(p, v, s)
    except Exception:  # fail closed on anything unexpected (INV-10)
        return Plan(PlanStatus.NO_PLAN)


def _read(p: Problem, v: dict[str, Any], s: cp_model.CpSolver) -> Plan:
    out: list[Contribution] = []
    pay: dict[str, int] = {}
    xs: dict[str, cp_model.IntVar] = v["xs"]
    for o in p.own:
        amt = s.value(xs[o.source_id])
        if amt > 0:
            out.append(Contribution(o.source_id, o.kind, amt))
            pay[o.source_id] = amt
    fam_count = set()
    for f, c, _y, send, fee, recv in v["chan"]:
        sent = s.value(send)
        if sent > 0:
            out.append(
                Contribution(
                    f.source_id,
                    "family",
                    s.value(recv),
                    sent,
                    c.provider_id,
                    s.value(fee),
                    c.eta_hours,
                )
            )
            fam_count.add(f.source_id)
    savings = sum(c.amount_minor for c in out if c.kind == "savings")
    scaled = 0
    for f, _c, _y, send, _fee, recv in v["chan"]:
        scaled += s.value(send) * mid_num(f.mid) - s.value(recv) * MID_SCALE
    fam_recv = sum(c.amount_minor for c in out if c.kind == "family")
    plan = Plan(
        PlanStatus.OK,
        tuple(out),
        ceil_div(scaled, MID_SCALE),
        fam_recv,
        savings,
        len(fam_count),
        pay,
    )
    covered = sum(pay.values()) + sum(c.amount_minor for c in out if c.kind == "family")
    if (
        covered < p.amount_minor
    ):  # cannot happen if the model is right: never return an underfunded plan
        return Plan(PlanStatus.NO_PLAN)
    return plan


def brute_force(p: Problem) -> tuple[int, int, int, int] | None:
    """The best (cost, family received, savings used, family sources) over every allocation
    (tests only, point by point).

    Exponential: use on tiny instances. Uses ``Channel.cost`` for the fee arithmetic, so it checks
    the
    solver's linear encoding against the plain Python rules."""
    import itertools

    chans = [
        (f, c) for f in p.family for c in f.channels
        if c.eta_hours <= p.hours_until_due and min(c.cap, f.limit_minor) >= c.min_send
    ]  # fmt: skip
    own_ranges = [range(0, min(o.available, p.amount_minor) + 1) for o in p.own]
    send_ranges = [[0, *range(c.min_send, min(c.cap, f.limit_minor) + 1)] for f, c in chans]
    best: tuple[int, int, int, int] | None = None
    for xs in itertools.product(*own_ranges):
        if sum(xs) > p.amount_minor:
            continue
        for sends in itertools.product(*send_ranges):
            per_family: dict[str, int] = {}
            for (f, _c), sd in zip(chans, sends, strict=True):
                per_family[f.source_id] = per_family.get(f.source_id, 0) + sd
            if any(per_family[f.source_id] > f.limit_minor for f, _ in chans):
                continue
            recv = cost = 0
            for (f, c), sd in zip(chans, sends, strict=True):
                if sd == 0:
                    continue
                _fee, delivered = c.cost(sd, f.mid)
                recv += delivered
                cost += sd * mid_num(f.mid) - delivered * MID_SCALE
            if sum(xs) + recv < p.amount_minor:
                continue
            sav = sum(x for x, o in zip(xs, p.own, strict=True) if o.kind == "savings")
            fams = sum(1 for sid in {f.source_id for f, _ in chans} if per_family.get(sid, 0) > 0)
            key = (ceil_div(cost, MID_SCALE), recv, sav, fams)
            if best is None or key < best:
                best = key
    return best


__all__ = [
    "Contribution",
    "FamilySource",
    "OwnSource",
    "Plan",
    "PlanStatus",
    "Problem",
    "brute_force",
    "ceil_div",
    "solve",
]


def load_spending_buffers() -> dict[str, int]:
    """Minimum spending balance to leave per currency (``config/funding.yaml``, an assumption)."""
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parents[3] / "config" / "funding.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {str(k): int(v) for k, v in raw["spending_buffer_minor"].items()}
