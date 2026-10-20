"""INV-12: nothing the agent sends to a model contains a secret, a full account number or a long id."""

from sqlalchemy import Engine

from agent_fakes import ScriptedGateway
from agent_helpers import collect
from corridor.agent.service import AgentService
from corridor.llm.guard import SecretRegistry, scan_text
from corridor.sandbox.vault import register_sandbox_secrets


async def test_every_prompt_passes_the_guard(
    service: AgentService, gateway: ScriptedGateway, sandbox: Engine
) -> None:
    registry = SecretRegistry()
    register_sandbox_secrets(registry, secrets_dir=sandbox.secrets_dir)  # type: ignore[attr-defined]
    for user, text in [
        ("usr_ramesh", "Ama lai 20 hajar pathau"),
        ("usr_priya", "how much money do I have"),
        ("usr_priya", "Pay my tuition invoice before the due date"),
        ("usr_anil", "What is my CNY balance?"),
    ]:
        await collect(service.chat(user, text))
    prompts = gateway.prompts()
    assert prompts, "the scripted gateway saw no prompts"
    for p in prompts:
        scan_text(p, registry)  # raises PromptLeakError on a secret, a 9+ digit run or a TOTP code
