"""Model gateway: one interface for all model calls with record and replay."""

from corridor.llm.client import Gateway, LLMResult, ToolCall, complete, get_gateway
from corridor.llm.config import ModelRole, load_models_config
from corridor.llm.errors import (
    CassetteMiss,
    LLMConfigError,
    LLMError,
    LLMSchemaError,
    LLMTimeout,
    LLMUnavailable,
    PromptLeakError,
    QuotaExhausted,
)
from corridor.llm.guard import SecretRegistry

__all__ = [
    "CassetteMiss",
    "Gateway",
    "LLMConfigError",
    "LLMError",
    "LLMResult",
    "LLMSchemaError",
    "LLMTimeout",
    "LLMUnavailable",
    "ModelRole",
    "PromptLeakError",
    "QuotaExhausted",
    "SecretRegistry",
    "ToolCall",
    "complete",
    "get_gateway",
    "load_models_config",
]
