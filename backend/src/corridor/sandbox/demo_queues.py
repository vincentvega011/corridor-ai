"""Give the demo's bank console something to show: ``python -m corridor.sandbox.demo_queues --api
URL``.

Runs against a running demo API, as the customer would, so everything goes through the normal
code paths (reader, agent, policy, confirmation, executor, ledger): no direct SQL for state the
executor owns.

* The genuine landlord's document is read, saved as a held payee with the authenticator code,
  and the bank is asked to verify it: one pending verification request whose Confirmation of
  Payee check matches.
* The two housing scam documents (a PDF invoice and a forwarded message) each produce a proposal
  that is shown but NOT confirmed: they appear in the ledger and the trace, and no payee or
  money state is created, so the customer scam demo still starts from the same place.
* A button payment to the held landlord is refused by policy: one denied proposal for the risk
  queue and the trace of a button action.

Sandbox only (the API refuses the demo authenticator route outside dev and demo). Synthetic data
only.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from corridor.sandbox.vault import demo_tokens

FIX = Path(__file__).resolve().parents[4] / "data" / "fixtures" / "rt"
USER = "usr_priya"


def _events(text: str) -> list[dict[str, Any]]:
    out = []
    for line in text.splitlines():
        if line.startswith("data:"):
            with contextlib.suppress(ValueError):
                out.append(json.loads(line[5:].strip()))
    return out


class Client:
    def __init__(self, api: str) -> None:
        self.api = api.rstrip("/")
        self.h = {"Authorization": f"Bearer {demo_tokens()[USER]}"}
        self.c = httpx.Client(timeout=120)

    def upload(self, name: str) -> str:
        time.sleep(7)  # the upload route allows 10 per minute
        r = self.c.post(
            f"{self.api}/attachments", headers=self.h, content=(FIX / name).read_bytes()
        )
        r.raise_for_status()
        return str(r.json()["doc_id"])

    def chat(self, body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        r = self.c.post(f"{self.api}/chat", headers=self.h, json=body)
        r.raise_for_status()
        return r.headers.get("x-thread-id", ""), _events(r.text)

    def code(self) -> str:
        r = self.c.get(f"{self.api}/demo/authenticator", headers=self.h)
        r.raise_for_status()
        return str(r.json()["code"])

    def confirm(self, thread: str, card: dict[str, Any]) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"proposal_hash": card["proposal_hash"]}
        if card.get("required_level") == "mfa":
            body["totp"] = self.code()
        r = self.c.post(f"{self.api}/chat/{thread}/confirm", headers=self.h, json=body)
        r.raise_for_status()
        return _events(r.text)


def seed(api: str) -> dict[str, str]:
    cl = Client(api)
    done: dict[str, str] = {}
    # 1. the genuine landlord: saved (held) and sent to the bank's queue
    doc = cl.upload("rt_housing_genuine.pdf")
    thread, ev = cl.chat(
        {"message": "Please pay this deposit for the room", "attachments": [{"doc_id": doc}]}
    )
    card = next((e["data"] for e in ev if e["type"] == "confirm_request"), None)
    if card is None:
        raise RuntimeError(
            "no confirmation card for the genuine landlord (replay cassette missing?)"
        )
    cl.confirm(thread, card)
    payees = cl.c.get(f"{cl.api}/payees", headers=cl.h).json()["payees"]
    landlord = next(p for p in payees if str(p["account_no"]).endswith("6612"))
    cl.c.post(
        f"{cl.api}/payees/{landlord['payee_id']}/verification-request", headers=cl.h, json={}
    ).raise_for_status()
    done["verification_request"] = landlord["payee_id"]
    # 2. the housing scam proposals: shown, not confirmed
    for name, message in (
        ("rt_housing_deposit.pdf", "Please pay this deposit for the room"),
        ("rt_msg_housing_deposit.png", "Yo agent le deposit magyo, pathaideu"),
    ):
        d = cl.upload(name)
        cl.chat({"message": message, "attachments": [{"doc_id": d}]})
    done["scam_proposals"] = "2"
    # 3. a button payment to the held landlord, refused by policy (denied proposal, button trace)
    cl.chat(
        {
            "message": "Pay Wattle Lane Property AUD 200.00",
            "action": {
                "kind": "send_money",
                "payee_id": landlord["payee_id"],
                "amount_minor": 20000,
                "currency": "AUD",
                "language": "en",
            },
        }
    )
    done["denied_button_payment"] = "1"
    return done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    a = ap.parse_args()
    try:
        print("demo queues seeded:", seed(a.api))
    except Exception as exc:  # the demo still works without the seed
        print(f"demo queues NOT seeded: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
