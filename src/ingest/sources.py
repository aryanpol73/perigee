"""The public data sources Perigee ingests. See docs/data-sources.md for their terms."""

from __future__ import annotations

from dataclasses import dataclass

from src.config import Settings

GP_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=csv"
SATCAT_URL = "https://celestrak.org/pub/satcat.csv"
SPACE_WEATHER_URL = "https://celestrak.org/SpaceData/SW-Last5Years.csv"

GP = "gp"
SATCAT = "satcat"
SPACE_WEATHER = "space_weather"


@dataclass(frozen=True)
class Source:
    name: str
    # File stem in the raw archive -> URL it is downloaded from.
    files: dict[str, str]
    min_interval_h: float


def all_sources(settings: Settings) -> list[Source]:
    return [
        Source(
            SATCAT,
            {"satcat": SATCAT_URL},
            settings.satcat_min_interval_h,
        ),
        Source(
            SPACE_WEATHER,
            {"space_weather": SPACE_WEATHER_URL},
            settings.space_weather_min_interval_h,
        ),
        Source(
            GP,
            {group: GP_URL.format(group=group) for group in settings.gp_groups},
            settings.gp_min_interval_h,
        ),
    ]
