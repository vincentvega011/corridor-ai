"""Gateway configuration: roles, backends and prices, loaded from config/models.yaml."""

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field

from corridor.config import AppEnv, Settings
from corridor.llm.errors import LLMConfigError

DEFAULT_MODELS_YAML = Path(__file__).resolve().parents[3] / "config" / "models.yaml"


class ModelRole(StrEnum):
    """Every place a model is used. Roles map to backends in models.yaml, never in code."""

    PLANNER = "planner"
    PARSER = "parser"
    PARSER_FALLBACK = "parser_fallback"
    READER = "reader"
    COMPOSER = "composer"
    LOCAL_PLANNER = "local_planner"


class Price(BaseModel):
    """Integer micro-USD per 1M tokens (equal to USD per 1M tokens times 1,000,000)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_micro_usd_per_m: int = Field(ge=0)
    output_micro_usd_per_m: int = Field(ge=0)
    model: str


class Limits(BaseModel):
    """Client-side request limits for a provider, set below the published ones. 0 disables."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    requests_per_minute: int = Field(ge=0)
    requests_per_hour: int = Field(ge=0)


class Provider(BaseModel):
    """A place models are served from: base URL env, key env and limits."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool
    kind: Literal["cloud", "local"]
    base_url_env: str
    api_key_env: str | None = None
    default_model_env: str | None = None
    free_tier: bool = False  # prompts may be retained by the provider: synthetic data only
    limits: Limits


class Autostart(BaseModel):
    """How to start a local server on demand (mlx_lm.server)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    port: int = Field(gt=0, lt=65536)
    model_repo: str
    ready_timeout_s: int = Field(gt=0)


class RoleConfig(BaseModel):
    """Static configuration of one role."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    base_url_env: str | None = None  # overrides the provider's base URL env for this role
    model_env: str
    timeout_s: int = Field(gt=0)
    max_tokens: int = Field(gt=0)
    temperature_milli: int = Field(ge=0, le=2000)
    structured_mode: Literal["json_schema", "json_object", "prompt"]
    supports_tools: bool = False
    extra_body: dict[str, Any] = Field(default_factory=dict)
    price: Price
    fallback_roles: tuple[ModelRole, ...] = ()
    local_only: bool = False  # roles that read untrusted content may never use a cloud provider
    autostart: Autostart | None = None


class Defaults(BaseModel):
    """Retry policy shared by all roles."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    retries: int = Field(default=2, ge=0, le=5)
    backoff_ms: int = Field(default=500, ge=0)
    retry_after_max_s: int = Field(default=10, ge=0)


class ModelsConfig(BaseModel):
    """The whole models.yaml file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    defaults: Defaults = Field(default_factory=Defaults)
    providers: dict[str, Provider]
    roles: dict[ModelRole, RoleConfig]

    def check_references(self) -> None:
        """Every role must name a known provider; fallbacks must exist."""
        for role, rc in self.roles.items():
            if rc.provider not in self.providers:
                raise ValueError(f"role {role.value} names unknown provider {rc.provider}")
            for fb in rc.fallback_roles:
                if fb not in self.roles or fb is role:
                    raise ValueError(f"role {role.value} has invalid fallback {fb.value}")


class ResolvedRole(BaseModel):
    """A role with env values resolved. The api key is deliberately not stored here."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: ModelRole
    cfg: RoleConfig
    provider_name: str
    provider: Provider
    base_url: str
    model: str

    @property
    def backend(self) -> str:
        """``cloud`` or ``local``, from the provider kind."""
        return self.provider.kind

    @property
    def is_free(self) -> bool:
        """Free routes: model id ends with ``:free``. They never accrue cost."""
        return self.model.endswith(":free")


def load_models_config(path: Path | None = None) -> ModelsConfig:
    """Load and validate models.yaml. A malformed file raises LLMConfigError."""
    try:
        raw = yaml.safe_load((path or DEFAULT_MODELS_YAML).read_text(encoding="utf-8"))
        cfg = ModelsConfig.model_validate(raw)
        cfg.check_references()
        return cfg
    except Exception as exc:
        raise LLMConfigError(f"invalid models config: {type(exc).__name__}") from exc


def _setting(settings: Settings, env_name: str) -> str:
    value = getattr(settings, env_name.lower(), None)
    if value is None:
        raise LLMConfigError(f"unknown setting {env_name}")
    if hasattr(value, "get_secret_value"):
        return str(value.get_secret_value())
    return str(value)


def resolve_role(
    config: ModelsConfig,
    settings: Settings,
    role: ModelRole,
    provider: str | None = None,
    model: str | None = None,
) -> ResolvedRole:
    """Resolve env backed fields for ``role``, optionally forcing a provider and model.

    A disabled provider, an empty model id or a non URL base fails closed. When a provider
    other than the role's own is forced, the provider's default base URL and (unless ``model``
    is given) its default model env are used.
    """
    cfg = config.roles.get(role)
    if cfg is None:
        raise LLMConfigError(f"role {role.value} is not configured")
    forced = provider is not None or model is not None
    if forced and settings.app_env is AppEnv.DEMO:
        raise LLMConfigError("forcing a provider or model is refused when APP_ENV=demo")
    name = provider or cfg.provider
    prov = config.providers.get(name)
    if prov is None:
        raise LLMConfigError(f"unknown provider {name}")
    if not prov.enabled:
        raise LLMConfigError(f"provider {name} is disabled")
    if cfg.local_only and prov.kind != "local":
        raise LLMConfigError(f"role {role.value} may only use local providers (INV-3)")
    if model is not None and prov.free_tier and not model.strip().endswith(":free"):
        raise LLMConfigError("a free-tier provider only serves :free models")
    own = name == cfg.provider
    base_env = (cfg.base_url_env if own else None) or prov.base_url_env
    if model is not None:
        model_id = model.strip()
    else:
        model_env = cfg.model_env if own else prov.default_model_env
        if model_env is None:
            raise LLMConfigError(f"provider {name} has no default model; pass model=")
        model_id = _setting(settings, model_env).strip()
    base_url = _setting(settings, base_env).strip()
    if not model_id:
        raise LLMConfigError(f"model id is empty for role {role.value} on {name}")
    if not base_url.startswith(("http://", "https://")):
        raise LLMConfigError(f"{base_env} must be a full http(s) URL")
    if settings.app_env is not AppEnv.TEST:  # tests use fake hosts over plain http
        host = urlparse(base_url).hostname or ""
        if prov.kind == "local" and host not in ("localhost", "127.0.0.1", "::1"):
            raise LLMConfigError(f"{base_env} must be loopback for a local provider")
        if prov.kind == "cloud" and not base_url.startswith("https://"):
            raise LLMConfigError(f"{base_env} must use https for a cloud provider")
    return ResolvedRole(
        role=role,
        cfg=cfg,
        provider_name=name,
        provider=prov,
        base_url=base_url.rstrip("/"),
        model=model_id,
    )


def api_key_for(provider: Provider, settings: Settings) -> str:
    """Return the API key for a provider ("not-needed" for local). Never log the result."""
    if provider.api_key_env is None:
        return "not-needed"
    key = _setting(settings, provider.api_key_env)
    if not key:
        raise LLMConfigError(f"{provider.api_key_env} is empty")
    return key
