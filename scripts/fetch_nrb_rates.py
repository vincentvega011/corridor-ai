"""Fetch Nepal Rastra Bank published foreign exchange rates into data/fx/nrb_rates.json.

Source: https://www.nrb.org.np/api/forex/v1/rates (public NRB API behind the Forex page
https://www.nrb.org.np/forex/). Run once; the seed reads the saved JSON so seeding is offline and
deterministic. Only the currencies we use are kept. Rates are NPR per `unit` of foreign currency.
"""

import json
from datetime import date
from pathlib import Path

import httpx

URL = "https://www.nrb.org.np/api/forex/v1/rates"
KEEP = {"USD", "AUD", "QAR", "MYR", "CNY"}
DATES = ["2026-04-10", "2026-05-10", "2026-06-10", "2026-07-10", "2026-08-10", "2026-09-10", "2026-10-09"]


def fetch_day(d: str) -> dict | None:
    """Return the NRB rate record on ``d`` or the nearest earlier published day within 7 days."""
    r = httpx.get(
        URL,
        params={"from": d, "to": d, "per_page": 5, "page": 1},
        headers={"User-Agent": "corridor-ai-sandbox"},
        timeout=30,
    )
    payload = r.json()["data"]["payload"]
    return payload[0] if payload else None


def main() -> None:
    out = {"source": URL, "retrieved": str(date.today()), "unit_note": "NPR per `unit` foreign units", "days": []}
    for d in DATES:
        rec = fetch_day(d)
        if rec is None:
            # nearest earlier day: widen the window backwards
            r = httpx.get(URL, params={"from": "2026-01-01", "to": d, "per_page": 1, "page": 1},
                          headers={"User-Agent": "corridor-ai-sandbox"}, timeout=30)
            rec = r.json()["data"]["payload"][0]
        rates = {
            x["currency"]["iso3"]: {"unit": x["currency"]["unit"], "buy": x["buy"], "sell": x["sell"]}
            for x in rec["rates"]
            if x["currency"]["iso3"] in KEEP
        }
        out["days"].append({"requested": d, "date": rec["date"], "rates": rates})
        print(d, "->", rec["date"], sorted(rates))
    out_dir = Path(__file__).resolve().parents[1] / "data" / "fx"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "nrb_rates.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
