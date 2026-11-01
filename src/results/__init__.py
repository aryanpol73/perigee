"""Results bundles: what the API serves."""

from pathlib import Path

# The small bundle committed so a clean checkout runs (see src/results/fixture.py).
FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "results"
