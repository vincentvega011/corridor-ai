"""Reply composition (docs/spec/modules.md 5.15 step 12). TODO-P2: grounding verification of every number (INV-9).

The composer (role ``composer``) writes the reply in the user's language style from tool facts only.
Fixed system messages (cancelled, hash mismatch, rejections, clarifications) use deterministic templates
from ``data/lang/*/replies.yaml``. If the composer fails, a templated reply is used. For an executed
payment a deterministic receipt line is always appended, so the exact amounts shown never depend on the
model. Numbers in prompts are rendered by ``corridor.formatting`` (INV-12).
"""

import json
from pathlib import Path
from typing import Any

import structlog

from corridor.contracts.intent import LanguageTag
from corridor.contracts.llm_outputs import ComposerOutput
from corridor.contracts.tools import ToolResult
from corridor.llm import Gateway, LLMError, ModelRole
from corridor.llm.guard import mask_account_numbers, redact_digit_runs
from corridor.nlu.clarify import style_for
from corridor.nlu.lang_pack import LangPacks

log = structlog.get_logger("corridor.agent.compose")
PROMPT_PATH = Path(__file__).parent / "prompts" / "composer_v1.txt"


def template(key: str, language: LanguageTag, deva: bool, packs: LangPacks, **fmt: Any) -> str:
    """A fixed reply in the user's style."""
    style = style_for(language, deva)
    return packs.replies[key][style].format(**fmt)


def read_lines(results: dict[str, ToolResult]) -> str:
    """Deterministic, language neutral lines from read tool data (the composer's fallback)."""
    lines: list[str] = []
    for name, res in results.items():
        d = res.data
        if name == "get_balances":
            lines += [f"{a['account_id']}: {a['balance']}" for a in d.get("accounts", [])]
        elif name == "get_history":
            lines += [
                f"{t['date']}  {t['amount']}  {t['payee_id']}  {t['status']}"
                for t in d.get("transfers", [])[:10]
            ]
            if not d.get("transfers"):
                lines.append("0 transfers")
        elif name == "list_providers":
            lines += [
                f"{p['name']}: fee {p['fixed_fee']} + {p['fee_pct']} + margin {p['fx_margin']}, {p['eta_hours']} h"
                for p in d.get("providers", [])
            ]
        elif name == "compare_routes" and d.get("found"):
            rec = d.get("recommended")
            if rec:
                lines.append(
                    f"Cheapest on time: {rec['route']}, cost {rec['total_cost']}, receives "
                    f"{rec['received']}, arrives by {rec['arrives_by']}. "
                    f"Saving versus the most expensive route: {d['saving_vs_most_expensive']}."
                )
                lines += [
                    f"{a['route']}: cost {a['total_cost']}, receives {a['received']}, "
                    f"arrives by {a['arrives_by']}" + ("" if a["meets_deadline"] else " (too late)")
                    for a in d.get("alternatives", [])
                ]
            else:
                fa = d.get("fastest_available")
                lines.append(
                    "No route arrives before the deadline."
                    + (
                        f" The fastest is {fa['route']}, arriving by {fa['arrives_by']}."
                        if fa
                        else ""
                    )
                )
        elif name == "get_fx_quote" and d.get("found"):
            lines.append(
                f"{d['pair']} {d['mid_rate']} ({d['as_of']})"
                + (f": {d['amount']} = {d['converted_at_mid']}" if "amount" in d else "")
            )
        elif name == "list_invoices":
            lines += [
                f"{i['invoice_id']}: {i['amount']} due {i['due_date']}"
                for i in d.get("invoices", [])
            ]
    return "\n".join(lines) if lines else "-"


async def compose_read_reply(
    gw: Gateway, *, request_text: str, language: LanguageTag, deva: bool, results: dict[str, ToolResult],
    packs: LangPacks, provider: str | None = None, model: str | None = None,
    strict: tuple[str, ...] | None = None,
) -> tuple[str, str]:  # fmt: skip
    """Compose a reply for a read request. Returns (text, source) with source composer or template.

    ``strict`` is set on the single regeneration after the grounding verifier rejected a reply: it
    lists the numbers that were not in the facts and forbids estimates (INV-9)."""
    style = style_for(language, deva)
    facts = {name: res.data for name, res in results.items()}
    body = {
        "language_style": style,
        "outcome": "read_result",
        "user_request": mask_account_numbers(redact_digit_runs(request_text))[:300],
        "facts": facts,
    }
    if strict:
        body["strict_instruction"] = (
            "Your previous reply contained numbers that are not in facts: "
            + ", ".join(strict[:8])
            + ". Rewrite it using ONLY numbers copied exactly from facts. Never predict, estimate "
            "or compute new numbers; if the user asks about the future, say it cannot be known."
        )
    system = PROMPT_PATH.read_text(encoding="utf-8").strip()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(body, ensure_ascii=False)},
    ]
    try:
        res = await gw.complete(ModelRole.COMPOSER, messages, schema=ComposerOutput)
        if isinstance(res.parsed, ComposerOutput):
            return res.parsed.reply, "composer"
    except LLMError as exc:
        log.warning("composer_failed", error=type(exc).__name__)
    return read_lines(results), "template"


async def compose_executed_reply(
    gw: Gateway, *, language: LanguageTag, deva: bool, amount: str, payee: str, balance: str, packs: LangPacks,
) -> tuple[str, str]:  # fmt: skip
    """The reply for a completed payment: the exact figures come from the template receipt."""
    receipt = template(
        "executed", language, deva, packs, amount=amount, payee=payee, balance=balance
    )
    return receipt, "template"
