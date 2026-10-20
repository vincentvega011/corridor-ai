"""Probe which demo chat lines are answered from the recorded cassettes (offline) and which fall back.

Run against a demo started with `make demo` (LLM_REPLAY=1):

    cd backend && uv run python ../scripts/chat_coverage.py --api http://127.0.0.1:8000

Each line is sent as the first message of a new thread, nothing is confirmed, so no money moves. A line is a
MISS when the thread ends in the single honest fallback (`assistant_unavailable` or `planner_failed`), otherwise
it is a HIT (the agent answered, asked a question, showed a card or refused with a policy reason). Synthetic
data only. Writes a markdown table to stdout and, with --json, a machine readable copy.
"""

from __future__ import annotations

import argparse
import time
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "data" / "fixtures"
FALLBACK_REASONS = {"assistant_unavailable", "planner_failed"}

# (persona, case, message, attachment relative to data/fixtures or None, language)
LINES: list[tuple[str, str, str, str | None, str]] = [
    (
        "usr_priya",
        "t04 tuition",
        "Pay my tuition invoice before the due date",
        None,
        "en",
    ),
    (
        "usr_priya",
        "t14 routes",
        "What is the cheapest way to send AUD 2,000 to my family in Nepal?",
        None,
        "en",
    ),
    (
        "usr_priya",
        "RT-080 housing scam",
        "Please pay this deposit for the room",
        "rt/rt_housing_deposit.pdf",
        "en",
    ),
    (
        "usr_priya",
        "RT-081 housing message",
        "Yo agent le deposit magyo, pathaideu",
        "rt/rt_msg_housing_deposit.png",
        "ne_latn",
    ),
    (
        "usr_priya",
        "t11 genuine landlord",
        "Please pay this deposit for the room",
        "rt/rt_housing_genuine.pdf",
        "en",
    ),
    (
        "usr_priya",
        "t19 typed payee",
        "Add my new landlord Okafor Property, account 062000771234",
        None,
        "en",
    ),
    (
        "usr_priya",
        "t19 pay typed payee",
        "Pay Okafor Property AUD 2,000 for the rental deposit",
        None,
        "en",
    ),
    (
        "usr_ramesh",
        "t12 Dashain",
        "Ama lai 800 QAR dashain aghi pathau",
        None,
        "ne_latn",
    ),
    (
        "usr_ramesh",
        "t56 cheapest",
        "Ama lai 800 QAR pathauna sabai bhanda sasto kun cha?",
        None,
        "ne_latn",
    ),
    (
        "usr_ramesh",
        "RT-017 cousin message",
        "Dinesh dai le pathaeko message ho, tyo account ma pathaideu",
        "message_cousin.png",
        "ne_latn",
    ),
    (
        "usr_anil",
        "RT-069 scholarship fee",
        "छात्रवृत्ति शुल्क: Karki lai CNY 150 pathaunuhos",
        None,
        "ne",
    ),
]


def fallback_texts() -> set[str]:
    """The exact fallback replies (assistant unavailable, planner failed) in every language."""
    import yaml

    packs = yaml.safe_load((ROOT / "data" / "lang" / "ne" / "replies.yaml").read_text())
    out: set[str] = set()
    for key in ("assistant_unavailable", "planner_failed"):
        out.update(v for v in packs["templates"][key].values() if isinstance(v, str))
    return out


def sse_events(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                out.append(json.loads(line[5:].strip()))
            except ValueError:
                pass
    return out


def probe(
    client: httpx.Client, api: str, token: str, message: str, attach: str | None
) -> tuple[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    body: dict = {"message": message}
    if attach:
        path = FIX / attach
        if not path.exists():
            path = next(FIX.rglob(Path(attach).name))
        time.sleep(7)  # the upload endpoint allows 10 per minute
        up = client.post(
            f"{api}/attachments", headers=headers, content=path.read_bytes(), timeout=120
        )
        if up.status_code != 200:
            return "MISS", f"document not read offline (HTTP {up.status_code})"
        body["attachments"] = [{"doc_id": up.json()["doc_id"]}]
    r = client.post(f"{api}/chat", headers=headers, json=body, timeout=120)
    events = sse_events(r.text)
    kinds = [e.get("type") for e in events]
    final = next(
        ((e.get("data") or {}) for e in reversed(events) if e.get("type") == "message"), {}
    )
    if (final.get("text") or "") in fallback_texts():
        return "MISS", "single fallback message"
    if r.status_code != 200:
        return "ERROR", f"HTTP {r.status_code}"
    last = next((k for k in reversed(kinds) if k not in ("ledger_append", "done")), "?")
    text = ""
    for e in reversed(events):
        d = e.get("data") or {}
        text = str(d.get("text") or d.get("question") or d.get("summary") or "")
        if text:
            break
    text = " ".join(text.split())[:110]
    if text.startswith("I cannot show exact figures"):
        return "PARTIAL", f"{last}: figures withheld (grounding), the Routes screen has them"
    return "HIT", f"{last}: {text}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--secrets", default=str(ROOT / "secrets"))
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    tokens = json.loads((Path(a.secrets) / "demo_tokens.json").read_text())
    rows = []
    with httpx.Client() as c:
        for user, case, msg, attach, lang in LINES:
            status, note = probe(c, a.api, tokens[user], msg, attach)
            rows.append(
                {
                    "persona": user[4:],
                    "case": case,
                    "message": msg,
                    "attachment": attach,
                    "status": status,
                    "note": note,
                }
            )
    print("| Persona | Case | Message | Result |\n|---|---|---|---|")
    for r in rows:
        att = f" + `{Path(r['attachment']).name}`" if r["attachment"] else ""
        print(
            f"| {r['persona']} | {r['case']} | {r['message']}{att} | {r['status']}: {r['note']} |"
        )
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
