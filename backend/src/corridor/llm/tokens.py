"""Local token estimation for providers that omit the ``usage`` object (free routes).

Uses a cached Hugging Face tokenizer (Qwen3, already downloaded for the local parser) when it is
available offline, otherwise a documented heuristic of one token per 4 UTF-8 bytes, rounded up.
Either way the result is an estimate and the caller marks ``usage_estimated=True``.
"""

import json
from collections.abc import Iterable
from functools import lru_cache
from typing import Any

TOKENIZER_REPO = "mlx-community/Qwen3-1.7B-4bit"


@lru_cache(maxsize=1)
def _tokenizer() -> Any | None:
    try:
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        path = hf_hub_download(TOKENIZER_REPO, "tokenizer.json", local_files_only=True)
        return Tokenizer.from_file(path)
    except Exception:
        return None


def estimate_tokens(text: str) -> int:
    """Estimate the token count of ``text``."""
    tok = _tokenizer()
    if tok is not None:
        return len(tok.encode(text).ids)
    return -(-len(text.encode("utf-8")) // 4)


def estimate_messages(messages: Iterable[dict[str, Any]]) -> int:
    """Estimate the prompt tokens of chat messages."""
    parts: list[str] = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            parts.extend(str(p.get("text", "")) for p in c if isinstance(p, dict))
        for call in m.get("tool_calls") or []:
            parts.append(json.dumps(call, ensure_ascii=False))
    return estimate_tokens("\n".join(parts))
