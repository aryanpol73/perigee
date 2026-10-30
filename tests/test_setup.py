"""Phase 0 smoke tests: the environment is importable and wired up."""

import importlib

import pytest

REQUIRED_PACKAGES = ["fastapi", "pandas", "numpy", "sgp4", "scipy", "duckdb"]


@pytest.mark.parametrize("name", REQUIRED_PACKAGES)
def test_required_package_imports(name):
    importlib.import_module(name)


def test_src_main_imports():
    importlib.import_module("src.main")


def test_duckdb_runs_a_query():
    import duckdb

    assert duckdb.sql("SELECT 42").fetchall() == [(42,)]
