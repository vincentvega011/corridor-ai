"""Typed gateway failures. Every failure raises one of these, never partial data (INV-10)."""


class LLMError(Exception):
    """Base class for all gateway errors. Callers must treat any LLMError as fail closed."""


class LLMConfigError(LLMError):
    """The role is not configured (missing model id, bad base URL, unknown role)."""


class LLMTimeout(LLMError):
    """The call exceeded the role timeout."""


class LLMUnavailable(LLMError):
    """The backend could not be reached or kept failing, or rejected the request."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class QuotaExhausted(LLMUnavailable):
    """The provider quota is used up (client-side limiter or an HTTP 429 we will not wait for)."""

    def __init__(self, message: str, retry_after_s: int | None = None) -> None:
        super().__init__(message, status=429)
        self.retry_after_s = retry_after_s


class LLMSchemaError(LLMError):
    """The model output did not validate against the schema, even after one repair retry."""


class PromptLeakError(LLMError):
    """The INV-12 prompt guard blocked a request. Carries only the rule id, never content."""

    def __init__(self, rule_id: str) -> None:
        super().__init__(f"prompt blocked by guard rule {rule_id}")
        self.rule_id = rule_id


class CassetteMiss(LLMError):
    """Replay mode found no recorded response for this request."""

    def __init__(self, key: str, role: str) -> None:
        super().__init__(f"no cassette for role {role} key {key}")
        self.key = key
        self.role = role
