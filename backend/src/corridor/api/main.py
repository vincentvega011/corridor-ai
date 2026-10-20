"""Production entry point: ``uvicorn corridor.api.main:app``.

Builds the engine, the model gateway (with every sandbox secret registered with the prompt guard), the
Postgres checkpointer and the agent service. ``APP_ENV=test`` uses an in memory checkpointer.
"""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from corridor.ablation import resolve_from_settings
from corridor.agent.graph import Deps
from corridor.agent.service import AgentService, memory_checkpointer, postgres_checkpointer
from corridor.agent.settings import load_corridors
from corridor.api.app import create_app
from corridor.api.auth import TokenAuth
from corridor.config import AppEnv, CorridorMode, get_settings
from corridor.llm import get_gateway
from corridor.logging import configure_logging
from corridor.nlu.lang_pack import load_packs
from corridor.sandbox.db import get_engine
from corridor.sandbox.vault import demo_tokens, officer_tokens


def build() -> FastAPI:
    """Create the app. The checkpointer is opened in the lifespan so it closes cleanly."""
    configure_logging()
    settings = get_settings()
    ablations = resolve_from_settings(
        settings
    )  # refuses to start with any ABL_* flag in dev or demo
    engine = get_engine()
    gateway = get_gateway()
    tokens = demo_tokens()
    if settings.corridor_mode is not CorridorMode.FULL:
        raise RuntimeError(
            "the API serves the full agent only; the baseline runs via /compare and the eval runner"
        )
    compare = None
    if settings.app_env in (AppEnv.DEV, AppEnv.DEMO):
        from corridor.api.compare import build_compare

        compare = build_compare(settings, gateway)
    holder: dict[str, AgentService] = {}
    from corridor.clock import clock_from

    clock = clock_from(settings.demo_clock)
    if clock.demo and settings.app_env not in (AppEnv.DEV, AppEnv.DEMO):
        raise RuntimeError("DEMO_CLOCK is allowed only in dev and demo")

    class LazyService:
        """Delegates to the real service once the lifespan has opened the checkpointer."""

        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            return getattr(holder["svc"], name)

    app = create_app(
        settings=settings, service=LazyService(), engine=engine, auth=TokenAuth(tokens), today=clock.today, clock=clock, compare=compare,  # type: ignore[arg-type]
        officer_token=officer_tokens() if settings.app_env in (AppEnv.DEV, AppEnv.DEMO) else None,
    )  # fmt: skip

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            cm = (
                memory_checkpointer()
                if settings.app_env is AppEnv.TEST
                else postgres_checkpointer(settings.database_url)
            )
            saver = await stack.enter_async_context(cm)
            deps = Deps(
                engine,
                gateway,
                load_packs(),
                load_corridors(),
                ablations=ablations,
                now_fn=clock.now,
            )
            holder["svc"] = AgentService(deps, saver, clock=clock.today)
            yield

    app.router.lifespan_context = lifespan
    return app


app = build()
