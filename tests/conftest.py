from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.config import Settings
from src.ingest.sources import GP_URL, SATCAT_URL, SPACE_WEATHER_URL

FIXTURES = Path(__file__).parent / "fixtures"
# Shortly after the newest epoch in the fixtures.
NOW = datetime(2026, 10, 30, 18, 0, 0, tzinfo=UTC)
STATIONS_URL = GP_URL.format(group="stations")


@pytest.fixture
def gp_bytes() -> bytes:
    return (FIXTURES / "gp_stations.csv").read_bytes()


@pytest.fixture
def satcat_bytes() -> bytes:
    return (FIXTURES / "satcat_sample.csv").read_bytes()


@pytest.fixture
def sw_bytes() -> bytes:
    return (FIXTURES / "space_weather_sample.csv").read_bytes()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        gp_groups=("stations",),
        gp_min_objects=10,
        satcat_min_objects=10,
        space_weather_min_days=10,
    )


class FakeFetcher:
    """Stands in for HTTP: maps URL -> bytes, or an exception to raise."""

    def __init__(self, responses: dict[str, bytes | Exception]) -> None:
        self.responses = dict(responses)
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def good_responses(gp_bytes: bytes, satcat_bytes: bytes, sw_bytes: bytes) -> dict[str, bytes]:
    return {STATIONS_URL: gp_bytes, SATCAT_URL: satcat_bytes, SPACE_WEATHER_URL: sw_bytes}
