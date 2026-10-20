"""Health check: ping every role and print a table. Run with ``python -m corridor.llm.check``.

Prints role, provider, model, reachable, latency, a one line sample reply, whether tool calling
works (for roles that offer tools) and whether the usage object is present. Also prints the
client-side quota used per cloud provider. Never prints keys or full prompts. Each cloud role
costs 1 or 2 requests of free-tier quota. Exit code 1 if any configured role fails.

Options: ``--no-failover`` pings the configured provider only (no fallback to local).
"""

import asyncio
import sys

from corridor.contracts.tools import ToolSpec
from corridor.llm.client import Gateway
from corridor.llm.config import ModelRole, resolve_role
from corridor.llm.errors import LLMError

PING = [{"role": "user", "content": "Reply with the single word: pong"}]
TOOL_PING = [
    {
        "role": "user",
        "content": "What is the balance of account acc1? Use the tool, do not answer yourself.",
    }
]
TOOL = ToolSpec(
    name="get_balance",
    description="Get the balance of an account by id.",
    args_schema={
        "type": "object",
        "properties": {"account_id": {"type": "string"}},
        "required": ["account_id"],
    },
)


async def _check_role(gw: Gateway, role: ModelRole, failover: bool = True) -> list[str]:
    try:
        rr = resolve_role(gw.config, gw.settings, role)
    except LLMError as exc:
        return [role.value, "-", "-", "not configured", "-", str(exc)[:60], "-", "-"]
    provider = None if failover else rr.provider_name
    try:
        res = await gw.complete(role, PING, provider=provider)
        sample = " ".join((res.text or "").split())[:40]
        served = res.provider if res.failover_from is None else f"{res.provider} (failover)"
        row = [role.value, served, res.model, "yes", f"{res.latency_ms} ms", sample]
        usage = "estimated" if res.usage_estimated else "yes"
    except LLMError as exc:
        return [role.value, rr.provider_name, rr.model, "NO", "-", type(exc).__name__, "-", "-"]
    if rr.cfg.supports_tools:
        try:
            tres = await gw.complete(role, TOOL_PING, tools=[TOOL], provider=provider)
            row.append("yes" if any(c.name == "get_balance" for c in tres.tool_calls) else "no")
        except LLMError as exc:
            row.append(f"no ({type(exc).__name__})")
    else:
        row.append("n/a")
    row.append(usage)
    return row


async def main(argv: list[str] | None = None) -> int:
    """Check every role and print the table. Returns the process exit code."""
    failover = "--no-failover" not in (argv if argv is not None else sys.argv[1:])
    gw = Gateway()
    try:
        rows = [await _check_role(gw, r, failover) for r in ModelRole]
        quota = {
            name: gw.limiter.usage(name)
            for name, p in gw.config.providers.items()
            if p.enabled and p.limits.requests_per_minute > 0
        }
    finally:
        await gw.aclose()
    head = ["role", "provider", "model", "reachable", "latency", "sample reply", "tools", "usage"]
    table = [head, *rows]
    widths = [max(len(r[i]) for r in table) for i in range(len(head))]
    for i, r in enumerate(table):
        print("  ".join(c.ljust(widths[j]) for j, c in enumerate(r)))
        if i == 0:
            print("  ".join("-" * w for w in widths))
    for name, (per_min, per_hour) in quota.items():
        lim = gw.config.providers[name].limits
        print(
            f"quota {name}: {per_min}/{lim.requests_per_minute} last minute, "
            f"{per_hour}/{lim.requests_per_hour} last hour"
        )
    return 1 if any(r[3] == "NO" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
