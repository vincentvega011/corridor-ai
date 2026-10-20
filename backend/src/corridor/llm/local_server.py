"""Start the local MLX server on demand (used by the planner failover to local_planner)."""

import asyncio
import os
import re
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlparse

import structlog

from corridor.llm.config import Autostart
from corridor.llm.errors import LLMUnavailable

log = structlog.get_logger("corridor.llm.local")
BACKEND_DIR = Path(__file__).resolve().parents[3]
LOG_DIR = BACKEND_DIR.parent / "logs"
_SECRET_ENV = re.compile(r"KEY|TOKEN|SECRET|PASSWORD", re.IGNORECASE)
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

Starter = Callable[[Autostart, str], Awaitable[bool]]


async def _listening(host: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=1)
    except (OSError, TimeoutError):
        return False
    writer.close()
    return True


async def ensure_running(auto: Autostart, base_url: str) -> bool:
    """Make sure an mlx_lm.server is listening at ``base_url``. Returns True if it was started.

    Only localhost URLs are ever started. Raises LLMUnavailable if it does not come up in time.
    """
    u = urlparse(base_url)
    host, port = u.hostname or "", u.port or auto.port
    if host not in LOCAL_HOSTS:
        return False
    if await _listening(host, port):
        return False
    log.info("local_server_starting", repo=auto.model_repo, port=port)
    exe = shutil.which("uv") or "uv"
    cmd = [exe, "run", "--group", "local", "mlx_lm.server"]
    cmd += ["--model", auto.model_repo, "--host", "127.0.0.1", "--port", str(port)]
    # The child never receives API keys or tokens from our environment.
    env = {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}
    try:
        LOG_DIR.mkdir(exist_ok=True)
        with open(LOG_DIR / "local_planner.log", "ab") as logfile:
            proc = subprocess.Popen(  # noqa: S603
                cmd,
                cwd=BACKEND_DIR,
                env=env,
                stdout=logfile,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
    except OSError as exc:
        raise LLMUnavailable("could not start the local server") from exc
    for _ in range(auto.ready_timeout_s):
        await asyncio.sleep(1)
        if await _listening(host, port):
            return True
    proc.terminate()  # do not leave an orphan that never became ready
    raise LLMUnavailable(f"local server on port {port} did not start in {auto.ready_timeout_s}s")


__all__ = ["Starter", "ensure_running"]
