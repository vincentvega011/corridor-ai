"""Ablation flags for the evaluation runner (docs/spec/architecture.md 4.3, ADR 0010).

Each flag removes exactly one defence from the full agent so the evaluation can show what that defence
contributes. They exist only to measure the system, so they are fenced three ways:

1. ``Ablations`` validates itself on construction: any active flag requires ``APP_ENV`` to be ``test``
   or ``eval`` and the database name to be an evaluation or test database (never the demo sandbox);
2. the API entry point resolves the flags from the environment at startup and refuses to start when any
   flag is set under ``dev`` or ``demo`` (``resolve_from_settings``);
3. the hooks live in the callers (graph, executor, service), never in ``corridor.policy``,
   ``corridor.taint`` or the other safety packages, so those packages stay pure.

What each flag removes (and nothing else):

- ``NO_QUARANTINE``: pasted third party text and the reader's field text reach the parser and planner as text.
- ``NO_TAINT``: provenance of proposal values is rewritten to SYSTEM before policy runs, so no taint rule fires.
- ``NO_POLICY``: the rule engine is replaced by its floor (``REQUIRE_CONFIRM``); the user still confirms.
- ``NO_GRANTS``: confirmation is not bound to a proposal hash and the executor does not consume a grant.
- ``NO_GROUNDING``: the reply is shown without the numeric grounding check.
- ``NO_COOLOFF``: the rule engine runs with every rule except R-PAYEE-COOLOFF (the fooled-user control).
- ``NO_RISK``: removes the deterministic risk score (R-RISK sees no weights and no carried score).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy.engine import make_url

from corridor.config import AppEnv, Settings


class Ablation(StrEnum):
    """The removable defences (the six of docs/spec/architecture.md 4.3 plus the payee cooling-off, ADR 0006)."""

    NO_QUARANTINE = "no_quarantine"
    NO_TAINT = "no_taint"
    NO_POLICY = "no_policy"
    NO_GRANTS = "no_grants"
    NO_GROUNDING = "no_grounding"
    NO_RISK = "no_risk"
    NO_COOLOFF = "no_cooloff"


class AblationRefused(Exception):
    """An ablation was requested where it is not allowed. The process must not start or run."""


ALLOWED_ENVS = (AppEnv.TEST, AppEnv.EVAL)
MAIN_SANDBOX_NAMES = frozenset({"corridor"})
# exact names only: a substring match would accept a production database such as "contest_prod"
EVAL_DATABASES = frozenset({"corridor_test", "corridor_eval", "corridor_eval_baseline"})


def database_name(url: str) -> str:
    """The database name inside a SQLAlchemy URL ('' if there is none)."""
    return make_url(url).database or ""


@dataclass(frozen=True)
class Ablations:
    """A validated set of active ablations. Construct it directly or with ``resolve_from_settings``."""

    active: frozenset[Ablation] = frozenset()
    app_env: AppEnv = AppEnv.PRODUCTION
    database_url: str = ""

    def __post_init__(self) -> None:
        if not self.active:
            return
        if self.app_env not in ALLOWED_ENVS:
            raise AblationRefused(f"ablations are refused when APP_ENV={self.app_env.value}")
        name = database_name(self.database_url)
        if name in MAIN_SANDBOX_NAMES or name not in EVAL_DATABASES:
            raise AblationRefused("ablated runs must use an evaluation database, never the sandbox")

    def has(self, flag: Ablation) -> bool:
        """Whether ``flag`` is active."""
        return flag in self.active

    @property
    def names(self) -> tuple[str, ...]:
        """Sorted flag names, for reports and logs."""
        return tuple(sorted(a.value for a in self.active))

    @classmethod
    def of(cls, flags: Iterable[Ablation | str], app_env: AppEnv, database_url: str) -> Ablations:
        """Build a validated set from flag names or members."""
        return cls(frozenset(Ablation(f) for f in flags), app_env, database_url)


NONE = Ablations()

LEAVE_ONE_IN_PREFIX = "only_"
NO_DEFENCES = "no_defences"


def leave_one_in_modes() -> tuple[str, ...]:
    """Evaluation mode names for the leave-one-in study: each defence alone, then no defences at all."""
    return (*(LEAVE_ONE_IN_PREFIX + a.value.removeprefix("no_") for a in Ablation), NO_DEFENCES)


def flags_for_mode(mode: str) -> tuple[Ablation, ...]:
    """The ablation flags a mode name stands for (empty for ``full`` and ``baseline``).

    ``no_<defence>`` removes one defence. ``only_<defence>`` keeps exactly one defence and removes every
    other (leave-one-in). ``no_defences`` removes all of them. Used by the evaluation runner only."""
    if mode in ("full", "baseline"):
        return ()
    if mode == NO_DEFENCES:
        return tuple(Ablation)
    if mode.startswith(LEAVE_ONE_IN_PREFIX):
        kept = Ablation("no_" + mode.removeprefix(LEAVE_ONE_IN_PREFIX))
        return tuple(a for a in Ablation if a is not kept)
    return (Ablation(mode),)


def resolve_from_settings(settings: Settings) -> Ablations:
    """The ablations requested by the environment, or ``AblationRefused`` when not allowed.

    Called once at API startup, so a stray ``ABL_*`` variable in dev or demo stops the server."""
    flags: list[Ablation] = [
        a
        for a, on in (
            (Ablation.NO_QUARANTINE, settings.abl_no_quarantine),
            (Ablation.NO_TAINT, settings.abl_no_taint),
            (Ablation.NO_POLICY, settings.abl_no_policy),
            (Ablation.NO_GRANTS, settings.abl_no_grants),
            (Ablation.NO_GROUNDING, settings.abl_no_grounding),
            (Ablation.NO_RISK, settings.abl_no_risk),
            (Ablation.NO_COOLOFF, settings.abl_no_cooloff),
        )
        if on
    ]
    return Ablations.of(flags, settings.app_env, settings.database_url)


def strip_taint(proposal_json: dict[str, Any]) -> dict[str, Any]:
    """Rewrite every provenance in a dumped proposal to ``system`` (used only by ``NO_TAINT``)."""

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "provenance" in node and "value" in node:
                return {**{k: walk(v) for k, v in node.items()}, "provenance": "system"}
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    out = walk(proposal_json)
    assert isinstance(out, dict)
    return out
