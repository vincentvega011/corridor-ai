"""Cost replay (ILLUSTRATIVE): the seeded remittance history priced two ways.

    cd backend && uv run python ../scripts/cost_replay.py [--json logs/cost_replay.json]

Every seeded remittance transfer (data/seeds/users.yaml, sandbox database) is priced again with the same
illustrative provider terms (data/seeds/providers.yaml) under
  (a) the naive default: BankWire every time (when BankWire cannot take that amount the configured
      default provider is used instead), and
  (b) the exact optimizer: the cheapest single channel (one transfer per payment, as the agent proposes).
Fees are converted to NPR at each transfer's own mid rate. The numbers are not live data and not a claim
about any real provider; they show what the optimizer does on invented but plausible terms.
"""

import argparse
import json
from decimal import Decimal

from sqlalchemy import select

from corridor.agent.settings import load_corridors
from corridor.contracts import CurrencyCode
from corridor.formatting import format_minor
from corridor.optimizer.routes import Channel, compare
from corridor.sandbox.db import get_engine
from corridor.sandbox.models import Provider, Transfer

NAIVE = "BankWire"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    cfg = load_corridors()
    eng = get_engine()
    rows: list[dict[str, object]] = []
    with eng.connect() as conn:
        from sqlalchemy.orm import Session

        with Session(conn) as s:
            provs = s.scalars(select(Provider).where(Provider.active.is_(True))).all()
            chans: dict[tuple[str, str], list[Channel]] = {}
            for p in provs:
                chans.setdefault((p.corridor_from, p.corridor_to), []).append(
                    Channel(p.id, p.name, p.fee_fixed_minor, p.fee_pct_bps, p.fx_margin_bps,
                            p.min_minor, p.max_minor, p.daily_limit_minor, p.eta_hours)
                )  # fmt: skip
            for t in s.scalars(select(Transfer).order_by(Transfer.id)).all():
                r = t.route_json
                if r.get("type") != "remittance":
                    continue
                pair = (t.currency, str(r["delivered_currency"]))
                mid = Decimal(str(r["mid_rate"]))
                cs = chans[pair]
                naive = next((c for c in cs if c.name == NAIVE and c.min_send <= t.amount_minor <= c.cap), None)
                if naive is None:
                    default_id = cfg.default_providers[f"{pair[0]}/{pair[1]}"]
                    naive = next(c for c in cs if c.provider_id == default_id)
                naive_fee = naive.cost(t.amount_minor, mid)[0]
                best = compare(cs, mid, t.amount_minor, None, max_legs=1).best
                if best is None:
                    continue
                saved_src = max(naive_fee - best.fee, 0)
                rows.append(
                    {
                        "transfer": t.id, "pair": f"{pair[0]}/{pair[1]}", "amount_minor": t.amount_minor,
                        "naive_fee_minor": naive_fee, "naive_provider": naive.name,
                        "optimized_fee_minor": best.fee, "optimized_provider": best.legs[0].name,
                        "saved_minor": saved_src, "mid": str(mid),
                        "naive_fee_npr_minor": int((Decimal(naive_fee) * mid).to_integral_value()),
                        "optimized_fee_npr_minor": int((Decimal(best.fee) * mid).to_integral_value()),
                        "saved_npr_minor": int((Decimal(saved_src) * mid).to_integral_value()),
                    }
                )  # fmt: skip
    total_npr = sum(int(r["saved_npr_minor"]) for r in rows)  # type: ignore[call-overload]
    by_pair: dict[str, int] = {}
    for r in rows:
        by_pair[str(r["pair"])] = by_pair.get(str(r["pair"]), 0) + int(r["saved_npr_minor"])  # type: ignore[call-overload]
    naive_total = sum(int(r["naive_fee_npr_minor"]) for r in rows)  # type: ignore[call-overload]
    opt_total = sum(int(r["optimized_fee_npr_minor"]) for r in rows)  # type: ignore[call-overload]
    pct = f"{(naive_total - opt_total) / naive_total * 100:.1f}%" if naive_total else "n/a"
    per_transfer = [
        {
            "transfer": r["transfer"], "pair": r["pair"],
            "naive": f"{r['naive_provider']} {format_minor(int(r['naive_fee_minor']), CurrencyCode(str(r['pair'])[:3]))}",  # type: ignore[call-overload]
            "optimized": f"{r['optimized_provider']} {format_minor(int(r['optimized_fee_minor']), CurrencyCode(str(r['pair'])[:3]))}",  # type: ignore[call-overload]
            "saved_npr": format_minor(int(r["saved_npr_minor"]), CurrencyCode.NPR),  # type: ignore[call-overload]
            "saved_pct_of_fee": f"{(1 - int(r['optimized_fee_minor']) / int(r['naive_fee_minor'])) * 100:.1f}%" if int(r["naive_fee_minor"]) else "n/a",  # type: ignore[call-overload]
        }
        for r in rows
    ]  # fmt: skip
    out = {
        "fees_naive_npr": format_minor(naive_total, CurrencyCode.NPR),
        "fees_optimized_npr": format_minor(opt_total, CurrencyCode.NPR),
        "saved_pct_of_fees": pct, "per_transfer": per_transfer,
        "label": "ILLUSTRATIVE: invented provider terms, seeded history, not live data",
        "transfers": len(rows), "total_saved_npr_minor": total_npr,
        "total_saved_npr": format_minor(total_npr, CurrencyCode.NPR),
        "by_corridor_npr": {k: format_minor(v, CurrencyCode.NPR) for k, v in sorted(by_pair.items())},
    }  # fmt: skip
    print(json.dumps(out, indent=2))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({**out, "rows": rows}, f, indent=2)


main()
