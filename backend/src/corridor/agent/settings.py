"""Agent level configuration loaded from backend/config/corridors.yaml."""

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "corridors.yaml"


@dataclass(frozen=True)
class CorridorConfig:
    """Corridor data: home currency per region, and the temporary default provider per corridor."""

    home_currency: dict[str, str]
    default_providers: dict[str, str]
    region_timezone: dict[str, str] = field(default_factory=dict)


@lru_cache(maxsize=2)
def load_corridors(path: Path | None = None) -> CorridorConfig:
    """Load corridors.yaml (cached)."""
    raw = yaml.safe_load((path or CONFIG_PATH).read_text(encoding="utf-8"))
    return CorridorConfig(
        home_currency=dict(raw["home_currency"]),
        default_providers=dict(raw["default_providers"]),
        region_timezone=dict(raw.get("region_timezone") or {}),
    )
