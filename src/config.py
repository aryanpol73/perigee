"""Settings read from environment variables, each with a documented default.

Every variable is listed in .env.example. Nothing here is a secret.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_GP_GROUPS = (
    "active",
    "analyst",
    "cosmos-2251-debris",
    "iridium-33-debris",
    "fengyun-1c-debris",
)
DEFAULT_USER_AGENT = "Perigee/0.1 (orbital debris risk tracker; github.com/aryanpol73/perigee)"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data")
    user_agent: str = DEFAULT_USER_AGENT
    gp_groups: tuple[str, ...] = DEFAULT_GP_GROUPS
    # CelesTrak usage policy: GP updates every 2 h, space weather every 3 h,
    # SATCAT once or twice a day. Never download more often than that.
    gp_min_interval_h: float = 2.0
    satcat_min_interval_h: float = 12.0
    space_weather_min_interval_h: float = 24.0
    http_timeout_s: float = 60.0
    http_max_attempts: int = 3
    # Data quality thresholds (see docs/data-sources.md).
    gp_min_objects: int = 10_000
    satcat_min_objects: int = 50_000
    space_weather_min_days: int = 1_000
    max_count_drop: float = 0.20
    max_bad_row_fraction: float = 0.01

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def quarantine_dir(self) -> Path:
        return self.data_dir / "quarantine"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    @classmethod
    def from_env(cls) -> Settings:
        groups = os.environ.get("PERIGEE_GP_GROUPS")
        return cls(
            data_dir=Path(os.environ.get("PERIGEE_DATA_DIR", "data")),
            user_agent=os.environ.get("PERIGEE_USER_AGENT", DEFAULT_USER_AGENT),
            gp_groups=tuple(g.strip() for g in groups.split(",") if g.strip())
            if groups
            else DEFAULT_GP_GROUPS,
            gp_min_interval_h=_env_float("PERIGEE_GP_MIN_INTERVAL_H", 2.0),
            satcat_min_interval_h=_env_float("PERIGEE_SATCAT_MIN_INTERVAL_H", 12.0),
            space_weather_min_interval_h=_env_float("PERIGEE_SW_MIN_INTERVAL_H", 24.0),
            http_timeout_s=_env_float("PERIGEE_HTTP_TIMEOUT_S", 60.0),
            http_max_attempts=_env_int("PERIGEE_HTTP_MAX_ATTEMPTS", 3),
            gp_min_objects=_env_int("PERIGEE_GP_MIN_OBJECTS", 10_000),
            satcat_min_objects=_env_int("PERIGEE_SATCAT_MIN_OBJECTS", 50_000),
            space_weather_min_days=_env_int("PERIGEE_SW_MIN_DAYS", 1_000),
            max_count_drop=_env_float("PERIGEE_MAX_COUNT_DROP", 0.20),
            max_bad_row_fraction=_env_float("PERIGEE_MAX_BAD_ROW_FRACTION", 0.01),
        )
