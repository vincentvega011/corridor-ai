"""Grounding verifier: every number shown to the user must trace to a same-turn fact (INV-9)."""

from corridor.grounding.extract import Found, Kind, extract_numbers
from corridor.grounding.registry import ToolResultRegistry, sig4
from corridor.grounding.verify import GroundingResult, ground_reply, verify_reply

__all__ = [
    "Found",
    "GroundingResult",
    "Kind",
    "ToolResultRegistry",
    "extract_numbers",
    "ground_reply",
    "sig4",
    "verify_reply",
]
