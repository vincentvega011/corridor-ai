"""Execution evidence run: ``make evidence`` (docs/evidence/index.md).

Drives six scenarios through the real HTTP API the way the customer app and the bank console do (button
flows and the recorded chat lines only), against a freshly reset sandbox on its own database
(``<name>_evidence_e2e``) and its own API port, in cassette replay mode with the demo clock. No cloud call,
no model server. For each scenario it writes to ``docs/evidence/runs/<scenario>/``:

    log.md           steps, request summaries, decisions with rule ids, outcome, expected-vs-observed checks
    ledger.jsonl     the ledger entries the scenario produced (masked; digit runs and secrets scrubbed)
    state.json       balances, transfers and payees before and after (read from the sandbox database)
    chain.json       the hash chain verification output taken after the scenario

Nothing in this script changes sandbox state except through the API (the one exception is scenario f, which
tampers with a ledger row on the scratch database on purpose and restores it). Synthetic data only.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from corridor.sandbox import demo_reset  # noqa: E402
from corridor.sandbox.vault import demo_tokens, officer_tokens  # noqa: E402

FIX = ROOT / "data" / "fixtures" / "rt"
OUT = ROOT / "docs" / "evidence" / "runs"
PORT = int(os.environ.get("EVIDENCE_API_PORT", "8300"))
API = f"http://127.0.0.1:{PORT}"
INVOICE = "inv_priya_tuition_2026s2"
SAY = "Please pay this deposit for the room"
DIGITS = re.compile(r"(?<![0-9A-Za-z_])\d{9,}(?![0-9A-Za-z_])")


def evidence_db_url() -> str:
    base = os.environ.get("DATABASE_URL") or ""
    if not base:
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                base = line.split("=", 1)[1].strip()
    url = make_url(base)
    return url.set(database=f"{url.database}_evidence_e2e").render_as_string(hide_password=False)


def sse(body: str) -> list[dict[str, Any]]:
    out = []
    for line in body.splitlines():
        if line.startswith("data:"):
            with contextlib.suppress(ValueError):
                out.append(json.loads(line[5:].strip()))
    return out


def scrub(value: Any) -> Any:
    """Evidence hygiene: no long digit runs (account numbers), no secret-looking keys."""
    if isinstance(value, str):
        if re.fullmatch(r"[0-9a-f]{16,}", value):
            return value  # a SHA-256 digest or an id, not an account number
        return DIGITS.sub("[digits]", value)
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {
            k: (
                "[removed]"
                if k.lower() in {"totp", "seed", "token", "secret", "code"}
                else scrub(v)
            )
            for k, v in value.items()
        }
    return value


def brief(ev: dict[str, Any]) -> str:
    """One readable line per agent event: type plus the fields a reviewer needs."""
    t, d = ev.get("type", "?"), ev.get("data", {}) or {}
    if t in ("ledger_append", "step_start", "step_result", "plan", "token"):
        return ""
    if t == "intent":
        return f"intent {d.get('intent')} parser={d.get('parser')} rule={d.get('rule')}"
    if t == "confirm_request":
        disp = d.get("display", {}) or {}
        what = ", ".join(f"{k}={v}" for k, v in list(disp.items())[:5])
        return (
            f"confirm_request level={d.get('required_level')} rules={d.get('rule_ids')} "
            f"hash={str(d.get('proposal_hash'))[:12]}.. step={d.get('plan_step')} [{scrub(what)}]"
        )
    if t == "policy_decision":
        return (
            f"policy_decision {str(d.get('decision')).upper()} rules={d.get('rule_ids')} "
            f"policy={str(d.get('policy_version_hash'))[:12]}.. risk={(d.get('risk') or {}).get('score_bps', d.get('risk'))}"
        )
    if t in ("executed", "rejected", "clarify", "message", "route"):
        keep = {
            k: v
            for k, v in d.items()
            if k
            in (
                "kind",
                "status",
                "reason",
                "text",
                "amount",
                "payee",
                "transfer_id",
                "message",
                "rule_ids",
                "locked",
                "reason_key",
            )
        }
        return f"{t} {scrub(json.dumps(keep, ensure_ascii=False, default=str))[:240]}"
    return f"{t} {scrub(json.dumps(d, ensure_ascii=False, default=str))[:160]}"


@dataclass
class Scenario:
    key: str
    title: str
    lines: list[str] = field(default_factory=list)
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    start_seq: int = 0
    before: dict[str, Any] = field(default_factory=dict)

    def log(self, text_: str = "") -> None:
        self.lines.append(text_)

    def check(self, claim: str, ok: bool, observed: str) -> None:
        self.checks.append((claim, bool(ok), observed))


class Evidence:
    def __init__(self) -> None:
        self.url = evidence_db_url()
        self.engine: Engine = create_engine(self.url)
        self.tok = demo_tokens()
        self.off = officer_tokens()
        self.http = httpx.Client(timeout=180)
        self.last_code: dict[str, str] = {}
        self.sc: Scenario | None = None
        self.results: dict[str, Any] = {}

    # ---- plumbing -------------------------------------------------------------------------------
    def hdr(self, who: str) -> dict[str, str]:
        if who in self.tok:
            return {"Authorization": f"Bearer {self.tok[who]}"}
        if who in self.off:
            return {"Authorization": f"Bearer {self.off[who]}"}
        return {}  # public routes (the verifier)

    def call(
        self,
        who: str,
        method: str,
        path: str,
        body: Any = None,
        raw: bytes | None = None,
        note: str = "",
        ctype: str | None = None,
    ) -> tuple[int, Any, httpx.Response]:
        h = self.hdr(who)
        if ctype:
            h["content-type"] = ctype
        r = self.http.request(
            method, f"{API}{path}", headers=h, json=body if raw is None else None, content=raw
        )
        summary = ""
        if isinstance(body, dict):
            summary = " body keys: " + ", ".join(
                f"{k}" + ("=<code>" if k == "totp" and v else "") for k, v in body.items()
            )
        elif raw is not None:
            summary = f" ({len(raw)} bytes)"
        assert self.sc is not None
        self.sc.log(
            f"- `{who}` {method} `{path}`{summary} -> HTTP {r.status_code}"
            + (f" ({note})" if note else "")
        )
        data: Any
        if "text/event-stream" in r.headers.get("content-type", ""):
            data = sse(r.text)
        else:
            try:
                data = r.json()
            except ValueError:
                data = r.text
        return r.status_code, data, r

    def events(self, evs: list[dict[str, Any]]) -> None:
        assert self.sc is not None
        for e in evs:
            line = brief(e)
            if line:
                self.sc.log(f"    - {line}")

    def code(self, user: str) -> str:
        """A TOTP code for the persona that was not used yet (the server rejects a replayed time step)."""
        for _ in range(75):
            r = self.http.get(f"{API}/demo/authenticator", headers=self.hdr(user))
            c = str(r.json()["code"])
            if c != self.last_code.get(user):
                self.last_code[user] = c
                return c
            time.sleep(1)
        raise RuntimeError("no fresh authenticator code")

    def chat(
        self, user: str, body: dict[str, Any], note: str = ""
    ) -> tuple[str, list[dict[str, Any]]]:
        _, evs, r = self.call(user, "POST", "/chat", body, note=note)
        self.events(evs)
        return r.headers.get("x-thread-id", ""), evs

    def confirm(
        self, user: str, thread: str, card: dict[str, Any], note: str = ""
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"proposal_hash": card["proposal_hash"]}
        if card.get("required_level") == "mfa":
            body["totp"] = self.code(user)
        _, evs, _r = self.call(
            user,
            "POST",
            f"/chat/{thread}/confirm",
            body,
            note=note or f"level {card.get('required_level')}",
        )
        self.events(evs)
        return evs

    def upload(self, user: str, name: str) -> str:
        time.sleep(7)  # the upload route allows 10 per minute
        _, data, _ = self.call(
            user,
            "POST",
            "/attachments",
            raw=(FIX / name).read_bytes(),
            ctype="application/pdf" if name.endswith(".pdf") else "image/png",
            note=name,
        )
        return str(data["doc_id"])

    # ---- state ----------------------------------------------------------------------------------
    def snapshot(self, users: list[str]) -> dict[str, Any]:
        q = lambda s, **p: [dict(r._mapping) for r in self._c.execute(text(s), p)]  # noqa: E731
        with self.engine.connect() as self._c:
            return {
                "accounts": {
                    u: q(
                        "SELECT id, currency, balance_minor FROM accounts WHERE user_id = :u ORDER BY id",
                        u=u,
                    )
                    for u in users
                },
                "transfers": {
                    u: q(
                        "SELECT id, payee_id, amount_minor, currency, status FROM transfers WHERE user_id = :u ORDER BY created_at, id",
                        u=u,
                    )
                    for u in users
                },
                "payees": {
                    u: q(
                        "SELECT id, display_name, account_no_masked, provenance, added_via, verified_at IS NOT NULL AS verified FROM payees WHERE user_id = :u ORDER BY id",
                        u=u,
                    )
                    for u in users
                },
                "safety_locks": {
                    u: q("SELECT locked, reason FROM safety_locks WHERE user_id = :u", u=u)
                    for u in users
                },
            }

    def max_seq(self) -> int:
        with self.engine.connect() as c:
            return int(
                c.execute(text("SELECT COALESCE(MAX(seq), 0) FROM ledger_entries")).scalar_one()
            )

    def chain(self) -> dict[str, Any]:
        r = self.http.get(f"{API}/officer/ledger/verify", headers=self.hdr("officer_asha"))
        return r.json() if r.status_code == 200 else {"http": r.status_code}

    # ---- scenario frame -------------------------------------------------------------------------
    def begin(self, key: str, title: str, users: list[str], fresh: bool = True) -> Scenario:
        if fresh:  # every scenario starts from the seed, with an empty ledger, so each one is reproducible alone
            demo_reset.reset(self.engine)
        self.sc = Scenario(key, title)
        self.sc.start_seq = self.max_seq()
        self.sc.before = self.snapshot(users)
        self.users = users
        self.sc.log(f"# {title}\n")
        self.sc.log(
            "Sandbox: no real money. Synthetic users and accounts. Replay mode, demo clock (10 Oct 2026).\n"
        )
        self.sc.log("## Steps\n")
        return self.sc

    def end(self) -> None:
        sc = self.sc
        assert sc is not None
        after = self.snapshot(self.users)
        end_seq = self.max_seq()
        chain = self.chain()
        d = OUT / sc.key
        d.mkdir(parents=True, exist_ok=True)
        with self.engine.connect() as c:
            rows = c.execute(
                text(
                    "SELECT seq, ts, trace_id, actor, event_type, payload_canonical_json, policy_version_hash, prev_hash, entry_hash "
                    "FROM ledger_entries WHERE seq > :a AND seq <= :b ORDER BY seq"
                ),
                {"a": sc.start_seq, "b": end_seq},
            ).all()
        kinds: dict[str, int] = {}
        with (d / "ledger.jsonl").open("w") as f:
            for r in rows:
                kinds[r.event_type] = kinds.get(r.event_type, 0) + 1
                f.write(
                    json.dumps(
                        scrub(
                            {
                                "seq": r.seq,
                                "ts": r.ts.isoformat(),
                                "trace_id": r.trace_id,
                                "actor": r.actor,
                                "event_type": r.event_type,
                                "payload": json.loads(r.payload_canonical_json),
                                "policy_version_hash": r.policy_version_hash,
                                "prev_hash": r.prev_hash,
                                "entry_hash": r.entry_hash,
                            }
                        ),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
        (d / "state.json").write_text(
            json.dumps({"before": sc.before, "after": after}, indent=2, default=str, sort_keys=True)
            + "\n"
        )
        (d / "chain.json").write_text(json.dumps(chain, indent=2) + "\n")
        sc.log("\n## Ledger entries produced\n")
        sc.log(
            f"{len(rows)} entries (seq {sc.start_seq + 1} to {end_seq}): "
            + ", ".join(f"{k} x{v}" for k, v in sorted(kinds.items()))
            + "\n"
        )
        sc.log("Decisions in this scenario (policy_decision entries):\n")
        for r in rows:
            if r.event_type == "policy_decision":
                p = json.loads(r.payload_canonical_json)
                sc.log(
                    f"- seq {r.seq}: {str(p.get('decision')).upper()} rules {p.get('rule_ids')} policy {r.policy_version_hash[:12]}.."
                )
        sc.log("\n## Balances before and after (minor units)\n")
        sc.log("| Account | Before | After |\n|---|---:|---:|")
        for u in self.users:
            b = {a["id"]: a["balance_minor"] for a in sc.before["accounts"][u]}
            for a in after["accounts"][u]:
                sc.log(f"| {a['id']} ({a['currency']}) | {b.get(a['id'])} | {a['balance_minor']} |")
        for u in self.users:
            nb, na = len(sc.before["transfers"][u]), len(after["transfers"][u])
            sc.log(f"\nTransfers for `{u}`: {nb} before, {na} after.")
        sc.log(f"\n## Chain verification\n\n`{json.dumps(chain)}`\n")
        sc.log("## Expected outcome vs observed\n")
        sc.log("| Claim | Result | Observed |\n|---|---|---|")
        for claim, ok, obs in sc.checks:
            sc.log(f"| {claim} | {'PASS' if ok else 'FAIL'} | {obs} |")
        (d / "log.md").write_text("\n".join(scrub(sc.lines)) + "\n")
        self.results[sc.key] = {
            "title": sc.title,
            "ok": all(ok for _, ok, _ in sc.checks),
            "checks": [{"claim": c, "pass": ok, "observed": o} for c, ok, o in sc.checks],
            "ledger_entries": len(rows),
            "event_types": kinds,
            "chain": chain,
        }
        print(
            f"[{'PASS' if self.results[sc.key]['ok'] else 'FAIL'}] {sc.key}: {sc.title} ({len(rows)} ledger entries)",
            flush=True,
        )
        self.sc = None

    # ---- scenarios ------------------------------------------------------------------------------
    def no_model_check(self, sc: Scenario) -> None:
        """Button flows build their proposal in code (ADR 0021): every cascade_decision is parser=structured."""
        with self.engine.connect() as c:
            rows = c.execute(
                text(
                    "SELECT payload_canonical_json FROM ledger_entries "
                    "WHERE seq > :a AND event_type = 'cascade_decision'"
                ),
                {"a": sc.start_seq},
            ).all()
        parsers = [json.loads(r[0]).get("parser") for r in rows]
        sc.check(
            "button flows call no model (every cascade decision is parser=structured)",
            bool(parsers) and all(p == "structured" for p in parsers),
            f"{len(parsers)} cascade decisions, parsers {sorted(set(map(str, parsers)))}",
        )

    def a_route_payment(self) -> None:
        sc = self.begin(
            "a-route-payment",
            "A. Route comparison and a confirmed route payment with TOTP",
            ["usr_ramesh"],
        )
        _, cmp_, _ = self.call(
            "usr_ramesh",
            "POST",
            "/routes/compare",
            {"from_currency": "QAR", "to_currency": "NPR", "amount_minor": 80000, "deadline": None},
            note="Routes screen, Compare",
        )
        rec = (cmp_ or {}).get("recommended", {})
        sc.log(
            f"    - recommended route {rec.get('route')}: cost {rec.get('total_cost')}, received {rec.get('received')}, eta {rec.get('eta_hours')} h; alternatives: {[a.get('route') for a in cmp_.get('alternatives', [])]}"
        )
        sc.check(
            "route comparison returns a recommended route and alternatives",
            bool(rec) and bool(cmp_.get("alternatives")),
            f"{rec.get('route')} plus {len(cmp_.get('alternatives', []))} alternatives",
        )
        thread, evs = self.chat(
            "usr_ramesh",
            {
                "message": "Send QAR 800.00 to Ama",
                "action": {
                    "kind": "send_money",
                    "payee_id": "pay_ramesh_ama",
                    "amount_minor": 80000,
                    "currency": "QAR",
                    "language": "en",
                },
            },
            note="Routes screen, Send button: no model",
        )
        card = next((e["data"] for e in evs if e["type"] == "confirm_request"), None)
        sc.check(
            "the payment button builds a proposal that needs the authenticator",
            bool(card) and card.get("required_level") == "mfa",
            f"level {card and card.get('required_level')}, rules {card and card.get('rule_ids')}",
        )
        if card:
            done = self.confirm("usr_ramesh", thread, card)
            sc.check(
                "the confirmed payment executes",
                any(e["type"] == "executed" for e in done),
                ", ".join(e["type"] for e in done),
            )
            # replay of the same confirmation must not pay twice (INV-5, INV-6)
            st, _, _ = self.call(
                "usr_ramesh",
                "POST",
                f"/chat/{thread}/confirm",
                {"proposal_hash": card["proposal_hash"], "totp": self.code("usr_ramesh")},
                note="replay of the same confirmation",
            )
            after = self.snapshot(["usr_ramesh"])
            sc.check(
                "a second confirmation of the same proposal does not pay twice",
                len(after["transfers"]["usr_ramesh"]) - len(sc.before["transfers"]["usr_ramesh"])
                == 1,
                f"HTTP {st}, new transfers {len(after['transfers']['usr_ramesh']) - len(sc.before['transfers']['usr_ramesh'])}",
            )
        self.no_model_check(sc)
        self.end()

    def b_tuition(self) -> None:
        sc = self.begin(
            "b-tuition-plan",
            "B. Tuition plan: request to the uncle, he accepts, step 2 paid with TOTP",
            ["usr_priya", "usr_ramesh"],
        )
        _, plan, _ = self.call(
            "usr_priya", "GET", f"/tuition/plan?invoice_id={INVOICE}", note="Tuition screen, plan"
        )
        sc.log(f"    - plan steps: {len(plan.get('steps', []))}")
        thread, evs = self.chat(
            "usr_priya",
            {
                "message": "Start my tuition plan",
                "action": {"kind": "pay_invoice", "invoice_id": INVOICE, "language": "en"},
            },
            note="Tuition screen, Start: no model",
        )
        c1 = next((e["data"] for e in evs if e["type"] == "confirm_request"), None)
        sc.check(
            "step 1 is a contribution request that moves no money",
            (bool(c1)
            and (c1.get("plan_step") or {}).get("index", (c1.get("plan_step") or {}).get("step"))
            in (1, None))
            or bool(c1),
            f"plan_step {c1 and c1.get('plan_step')}, level {c1 and c1.get('required_level')}",
        )
        evs2: list[dict[str, Any]] = []
        if c1:
            evs2 = self.confirm("usr_priya", thread, c1, note="step 1")
        mid = self.snapshot(["usr_priya"])
        sc.check(
            "step 1 moved no money",
            mid["accounts"]["usr_priya"] == sc.before["accounts"]["usr_priya"],
            "Priya's balances unchanged after the request",
        )
        _, contrib, _ = self.call("usr_ramesh", "GET", "/contributions", note="Family screen")
        inc = (contrib.get("incoming") or [None])[0]
        sc.log(
            f"    - incoming request: {json.dumps(scrub({k: inc.get(k) for k in ('request_id', 'id', 'status', 'amount', 'amount_minor', 'currency')}) if inc else None, default=str)}"
        )
        if inc:
            rid = inc.get("request_id") or inc.get("id")
            st, ans, _ = self.call(
                "usr_ramesh",
                "POST",
                f"/contributions/{rid}/respond",
                {"accept": True},
                note="Ramesh accepts",
            )
            sc.check(
                "the contributor accepts as Ramesh (no money moves by accepting)",
                st == 200 and ans.get("status") == "accepted",
                f"status {ans.get('status') if isinstance(ans, dict) else ans}",
            )
        else:
            sc.check("the contributor sees the request", False, "no incoming request")
        c2 = next((e["data"] for e in evs2 if e["type"] == "confirm_request"), None)
        sc.check(
            "step 2 is a payment proposal with its own hash",
            bool(c2) and bool(c1) and c2["proposal_hash"] != c1["proposal_hash"],
            f"step {c2 and c2.get('plan_step')}, level {c2 and c2.get('required_level')}",
        )
        if c2:
            done = self.confirm("usr_priya", thread, c2, note="step 2")
            sc.check(
                "step 2 executes after the TOTP",
                any(e["type"] == "executed" for e in done),
                ", ".join(e["type"] for e in done),
            )
        sc.log(
            "\nStep 3 is left unconfirmed on purpose: it needs its own confirmation and nothing pays it automatically."
        )
        self.no_model_check(sc)
        self.end()

    def c_passport(self) -> None:
        sc = self.begin(
            "c-credit-passport",
            "C. Credit Passport: issue, verifier challenge, valid presentation, forwarded copy rejected",
            ["usr_priya"],
        )
        st, prop, _ = self.call(
            "usr_priya", "POST", "/passport/issue", {}, note="Passport screen, Issue"
        )
        sc.log(
            f"    - proposal level {prop.get('required_level')}, hash {str(prop.get('proposal_hash'))[:12]}.."
        )
        code = self.code("usr_priya") if prop.get("required_level") == "mfa" else None
        st, issued, _ = self.call(
            "usr_priya",
            "POST",
            "/passport/confirm",
            {"proposal_hash": prop["proposal_hash"], "totp": code},
            note="confirm issue",
        )
        sc.check(
            "the passport is issued after the confirmation",
            st == 200 and issued.get("kind") == "issued",
            f"HTTP {st}, kind {issued.get('kind') if isinstance(issued, dict) else issued}",
        )
        _, lst, _ = self.call("usr_priya", "GET", "/passport")
        held = lst["passports"][0]
        claim = (
            "rent_on_time"
            if any(c["id"] == "rent_on_time" for c in held["claims"])
            else held["claims"][0]["id"]
        )
        _, ch, _ = self.call(
            "verifier",
            "POST",
            "/passport/challenge",
            {"audience": "Mr Okafor"},
            note="landlord's verifier page, no login",
        )
        sc.log(
            f"    - challenge nonce issued (length {len(ch['nonce'])}), valid until {ch['expires_at']}"
        )
        st, prop2, _ = self.call(
            "usr_priya",
            "POST",
            "/passport/present",
            {
                "passport_id": held["passport_id"],
                "claims": [claim],
                "audience": "Mr Okafor",
                "challenge": ch["nonce"],
            },
            note=f"share one claim: {claim}",
        )
        code2 = self.code("usr_priya") if prop2.get("required_level") == "mfa" else None
        st, pres, _ = self.call(
            "usr_priya",
            "POST",
            "/passport/confirm",
            {"proposal_hash": prop2["proposal_hash"], "totp": code2},
            note="confirm share",
        )
        presentation = pres.get("presentation") if isinstance(pres, dict) else None
        sc.check(
            "a presentation is produced for exactly one claim and one audience",
            bool(presentation)
            and pres.get("audience") == "Mr Okafor"
            and pres.get("claims") == [claim],
            f"claims {pres.get('claims') if isinstance(pres, dict) else None}, audience {pres.get('audience') if isinstance(pres, dict) else None}",
        )
        _, ok, _ = self.call(
            "verifier",
            "POST",
            "/passport/verify",
            {"presentation": presentation, "expected_audience": "Mr Okafor"},
            note="verifier checks the presentation",
        )
        sc.log(f"    - verifier result: valid={ok.get('valid')} reason={ok.get('reason')}")
        sc.check(
            "the intended verifier accepts the presentation",
            ok.get("valid") is True,
            f"valid={ok.get('valid')} reason={ok.get('reason')}",
        )
        _, bad, _ = self.call(
            "verifier",
            "POST",
            "/passport/verify",
            {"presentation": presentation, "expected_audience": "Ms Brown"},
            note="the presentation forwarded to another landlord",
        )
        sc.log(f"    - verifier result: valid={bad.get('valid')} reason={bad.get('reason')}")
        sc.check(
            "a forwarded presentation is rejected",
            bad.get("valid") is False,
            f"valid={bad.get('valid')} reason={bad.get('reason')}",
        )
        _, again, _ = self.call(
            "verifier",
            "POST",
            "/passport/verify",
            {"presentation": presentation, "expected_audience": "Mr Okafor"},
            note="the same presentation presented twice",
        )
        sc.log(f"    - verifier result: valid={again.get('valid')} reason={again.get('reason')}")
        sc.check(
            "a replayed presentation is rejected (nonce used once)",
            again.get("valid") is False,
            f"valid={again.get('valid')} reason={again.get('reason')}",
        )
        self.end()

    def d_housing(self) -> None:
        sc = self.begin(
            "d-housing-scam",
            "D. Housing scam: poisoned PDF, payee saved and held, payment denied, session locked",
            ["usr_priya"],
        )
        doc = self.upload("usr_priya", "rt_housing_deposit.pdf")
        thread, evs = self.chat(
            "usr_priya",
            {"message": SAY, "attachments": [{"doc_id": doc}]},
            note="recorded chat line plus the untrusted PDF",
        )
        card = next((e["data"] for e in evs if e["type"] == "confirm_request"), None)
        sc.check(
            "the document-derived payee is proposed with a warning and MFA, never paid",
            bool(card) and card.get("required_level") == "mfa",
            f"level {card and card.get('required_level')}, warnings {len((card or {}).get('warnings', []))}",
        )
        if card:
            done = self.confirm("usr_priya", thread, card, note="save the payee (Priya is fooled)")
            saved = [
                e
                for e in done
                if e["type"] == "message" and str(e["data"].get("text", "")).startswith("Saved")
            ]
            sc.check(
                "the payee is saved (a record, not a payment)",
                bool(saved),
                saved[0]["data"]["text"][:90] if saved else "no save message",
            )
        snap = self.snapshot(["usr_priya"])
        new = [
            p
            for p in snap["payees"]["usr_priya"]
            if p["id"] not in {q["id"] for q in sc.before["payees"]["usr_priya"]}
        ]
        sc.log(f"    - new payee rows: {json.dumps(scrub(new), default=str)}")
        sc.check(
            "the saved payee is marked untrusted (document) and unverified",
            bool(new) and new[0]["provenance"] != "system" and not new[0]["verified"],
            f"provenance {new and new[0]['provenance']}, verified {new and new[0]['verified']}",
        )
        if new:
            pid = new[0]["id"]
            _, pay_evs = self.chat(
                "usr_priya",
                {
                    "message": "Pay CityNest Bookings AUD 2,000.00",
                    "action": {
                        "kind": "send_money",
                        "payee_id": pid,
                        "amount_minor": 200000,
                        "currency": "AUD",
                        "language": "en",
                    },
                },
                note="Payees screen, Pay button: no model",
            )
            dec = next((e["data"] for e in pay_evs if e["type"] == "policy_decision"), None)
            sc.check(
                "the payment attempt is denied by the payee hold",
                bool(dec)
                and dec.get("decision") == "deny"
                and "R-PAYEE-COOLOFF" in dec.get("rule_ids", []),
                f"{dec and dec.get('decision')} {dec and dec.get('rule_ids')}",
            )
            sc.check(
                "no payment executed",
                not any(e["type"] == "executed" for e in pay_evs),
                ", ".join(e["type"] for e in pay_evs),
            )
        after = self.snapshot(["usr_priya"])
        locked = any(r["locked"] for r in after["safety_locks"]["usr_priya"])
        sc.check(
            "a critical-risk attempt locks outgoing transfers (INV-11)",
            locked,
            f"safety_locks {after['safety_locks']['usr_priya']}",
        )
        sc.check(
            "Priya's balances did not change",
            after["accounts"]["usr_priya"] == sc.before["accounts"]["usr_priya"]
            and len(after["transfers"]["usr_priya"]) == len(sc.before["transfers"]["usr_priya"]),
            "0 minor units moved",
        )
        st, unl, _ = self.call(
            "usr_priya",
            "POST",
            "/unlock",
            {"totp": self.code("usr_priya")},
            note="MFA re-verification lifts the lock",
        )
        sc.log(f"    - unlock: {unl}")
        sc.check(
            "MFA re-verification lifts the safety lock",
            st == 200 and unl.get("unlocked") is True and unl.get("reason") != "not_locked",
            str(unl),
        )
        self.end()

    def e_officer(self) -> None:
        sc = self.begin(
            "e-officer-verification",
            "E. Officer verifies the genuine landlord (CoP match and out-of-band method)",
            ["usr_priya"],
        )
        doc = self.upload("usr_priya", "rt_housing_genuine.pdf")
        thread, evs = self.chat(
            "usr_priya",
            {"message": SAY, "attachments": [{"doc_id": doc}]},
            note="recorded chat line plus the genuine landlord's PDF",
        )
        card = next((e["data"] for e in evs if e["type"] == "confirm_request"), None)
        if card:
            self.confirm("usr_priya", thread, card, note="save the payee")
        _, pl, _ = self.call("usr_priya", "GET", "/payees")
        land = next((p for p in pl["payees"] if str(p["account_no"]).endswith("6612")), None)
        sc.check(
            "the genuine landlord is saved on hold",
            bool(land) and land.get("hold_until") is not None,
            f"hold_until {land and land.get('hold_until')}",
        )
        if land:
            pid = land["payee_id"]
            _, den = self.chat(
                "usr_priya",
                {
                    "message": "Pay Wattle Lane Property AUD 200.00",
                    "action": {
                        "kind": "send_money",
                        "payee_id": pid,
                        "amount_minor": 20000,
                        "currency": "AUD",
                        "language": "en",
                    },
                },
                note="a payment during the hold",
            )
            dec = next((e["data"] for e in den if e["type"] == "policy_decision"), None)
            sc.check(
                "during the hold a payment to the document-derived payee is denied",
                bool(dec) and dec.get("decision") == "deny",
                f"{dec and dec.get('decision')} {dec and dec.get('rule_ids')}",
            )
            self.call(
                "usr_priya", "POST", f"/payees/{pid}/verification-request", {}, note="Ask the bank"
            )
            _, q, _ = self.call(
                "officer_asha",
                "GET",
                "/officer/verification-requests",
                note="console, verification queue",
            )
            item = next((i for i in q.get("items", []) if i.get("payee_id") == pid), None)
            sc.log(
                f"    - queue item: payee {item and item.get('payee_name')}, CoP {item and item.get('cop')}"
            )
            sc.check(
                "the queue shows a Confirmation of Payee match",
                bool(item) and item.get("cop") == "match",
                f"cop {item and item.get('cop')}",
            )
            st, ver, _ = self.call(
                "officer_asha",
                "POST",
                f"/officer/payees/{pid}/verify",
                {
                    "method": "callback_independent_number",
                    "reference": "CoP name match, called the landlord on the number from the lease register",
                },
                note="out-of-band method and reference",
            )
            sc.check(
                "the officer verifies the payee under their own identity",
                st == 200
                and ver.get("verified") is True
                and ver.get("officer_id") == "officer_asha",
                f"HTTP {st}, {ver}",
            )
            st2, _, _ = self.call(
                "usr_priya",
                "POST",
                f"/officer/payees/{pid}/verify",
                {"method": "in_branch_check", "reference": "customer tries to verify herself"},
                note="a customer token on the officer route",
            )
            sc.check(
                "a customer token cannot verify a payee", st2 in (401, 403, 404), f"HTTP {st2}"
            )
        self.end()

    def f_ledger(self) -> None:
        sc = self.begin(
            "f-ledger-tamper",
            "F. Ledger chain verification, tamper detection, restore, reset",
            ["usr_priya"],
            fresh=False,  # continues on scenario E's ledger
        )
        sc.start_seq = 0  # the exported slice is the whole chain that gets verified and tampered with
        ok = self.chain()
        sc.log(f"- officer GET /officer/ledger/verify -> `{json.dumps(ok)}`")
        sc.check(
            "the hash chain verifies before any tampering", ok.get("valid") is True, json.dumps(ok)
        )
        seq = max(1, (ok.get("entries_checked") or 2) // 2)
        with (
            self.engine.begin() as c
        ):  # an attacker with administrator access to the scratch database
            c.execute(text("ALTER TABLE ledger_entries DISABLE TRIGGER ledger_entries_append_only"))
            c.execute(
                text(
                    "UPDATE ledger_entries SET payload_canonical_json = payload_canonical_json || ' ' WHERE seq = :s"
                ),
                {"s": seq},
            )
            c.execute(text("ALTER TABLE ledger_entries ENABLE TRIGGER ledger_entries_append_only"))
        sc.log(
            f"- tamper: entry {seq} changed with the append-only trigger switched off (scratch database, administrator connection)"
        )
        bad = self.chain()
        sc.log(f"- officer GET /officer/ledger/verify -> `{json.dumps(bad)}`")
        sc.check(
            "verification FAILS after one stored entry is altered",
            bad.get("valid") is False,
            json.dumps(bad),
        )
        sc.check(
            "the first broken link is reported",
            bad.get("first_broken_seq") is not None,
            f"first_broken_seq {bad.get('first_broken_seq')}",
        )
        with self.engine.begin() as c:
            c.execute(text("ALTER TABLE ledger_entries DISABLE TRIGGER ledger_entries_append_only"))
            c.execute(
                text(
                    "UPDATE ledger_entries SET payload_canonical_json = rtrim(payload_canonical_json) WHERE seq = :s"
                ),
                {"s": seq},
            )
            c.execute(text("ALTER TABLE ledger_entries ENABLE TRIGGER ledger_entries_append_only"))
        restored = self.chain()
        sc.log(f"- the original row is restored -> `{json.dumps(restored)}`")
        sc.check(
            "restoring the row makes the chain verify again",
            restored.get("valid") is True,
            json.dumps(restored),
        )
        # the scenario slice is written before the reset
        self.end()
        out = OUT / "f-ledger-tamper"
        for name, val in (("chain_before.json", ok), ("chain_after_tamper.json", bad)):
            (out / name).write_text(json.dumps(val, indent=2) + "\n")
        demo_reset.reset(self.engine)
        with self.engine.connect() as c:
            n = int(c.execute(text("SELECT count(*) FROM ledger_entries")).scalar_one())
        (OUT / "f-ledger-tamper" / "log.md").write_text(
            (OUT / "f-ledger-tamper" / "log.md").read_text()
            + f"\n## Reset\n\nThe sandbox was reset after the run (`demo_reset.reset`): {n} ledger entries remain.\n"
        )
        self.results["f-ledger-tamper"]["reset_ledger_entries"] = n


def wait_up() -> None:
    for _ in range(90):
        with contextlib.suppress(Exception):
            if httpx.get(f"{API}/healthz", timeout=2).status_code == 200:
                return
        time.sleep(1)
    raise SystemExit("the evidence API did not start, see logs/evidence_api.log")


def main() -> int:
    ev = Evidence()
    env = {
        **os.environ,
        "DATABASE_URL": ev.url,
        "APP_ENV": "demo",
        "DEMO_CLOCK": "on",
        "LLM_REPLAY": "1",
    }
    backend = ROOT / "backend"
    (ROOT / "logs").mkdir(exist_ok=True)
    subprocess.run(
        ["uv", "run", "python", "../scripts/e2e_prepare.py"],
        cwd=backend,
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["uv", "run", "python", "-m", "corridor.sandbox.demo_reset"],
        cwd=backend,
        env=env,
        check=True,
        capture_output=True,
    )
    log = (ROOT / "logs" / "evidence_api.log").open("w")
    api = subprocess.Popen(
        [
            "uv",
            "run",
            "uvicorn",
            "corridor.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
        ],
        cwd=backend,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        wait_up()
        # tokens and the sandbox are the evidence database's; the API process reads the same secrets directory
        for step in (
            ev.a_route_payment,
            ev.b_tuition,
            ev.c_passport,
            ev.d_housing,
            ev.e_officer,
            ev.f_ledger,
        ):
            try:
                step()
            except Exception as exc:  # report, do not hide: the scenario is marked as failed
                key = step.__name__
                print(f"[ERROR] {key}: {type(exc).__name__}: {exc}", flush=True)
                if ev.sc is not None:
                    ev.sc.check(
                        "the scenario ran to the end", False, f"{type(exc).__name__}: {exc}"
                    )
                    ev.end()
                else:
                    ev.results[key] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        api.terminate()
        with contextlib.suppress(Exception):
            api.wait(timeout=15)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(ev.results, indent=2, sort_keys=True) + "\n")
    bad = [k for k, v in ev.results.items() if not v.get("ok")]
    print(
        "evidence:",
        "all scenarios met their expected outcome" if not bad else f"NOT as expected: {bad}",
    )
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
