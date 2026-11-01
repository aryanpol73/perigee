"""Downloading and uploading results bundles (public results dataset)."""

from __future__ import annotations

import json
import shutil
import sys
import types
from pathlib import Path

import pytest

from src.api.data import ResultsStore
from src.results import FIXTURE_DIR
from src.results.remote import POINTER, bundle_files, fetch_latest, push_bundle


def _fixture_run() -> Path:
    return FIXTURE_DIR / json.loads((FIXTURE_DIR / POINTER).read_text())["path"]


def _fake_hub(tmp_path: Path, repo: str) -> str:
    """Lay the fixture bundle out like a Hugging Face dataset and return a file:// base URL."""
    run = _fixture_run()
    root = tmp_path / "hub" / "datasets" / repo / "resolve" / "main"
    shutil.copytree(run, root / run.name)
    (root / POINTER).write_text(json.dumps({"run_id": run.name, "path": run.name}))
    return (tmp_path / "hub").as_uri()


def test_fetch_latest_downloads_a_loadable_bundle(tmp_path):
    base = _fake_hub(tmp_path, "someone/perigee-results")
    out = tmp_path / "results"
    out.mkdir()
    run_id = fetch_latest("someone/perigee-results", out, base=base)
    store = ResultsStore.load(out)
    assert store.meta["run_id"] == run_id and len(store.objects) > 100
    assert not list(out.glob(".*"))  # no temp files left behind
    # Running again with the bundle already here downloads nothing and keeps the pointer.
    shutil.rmtree(tmp_path / "hub")
    with pytest.raises(OSError):
        fetch_latest("someone/perigee-results", out, base=base)
    assert ResultsStore.load(out).meta["run_id"] == run_id


def test_manifest_with_unexpected_files_is_refused():
    with pytest.raises(ValueError):
        bundle_files({"orbit_file": "orbits.x.json.gz", "files": ["meta.json", "../../etc/passwd"]})
    with pytest.raises(ValueError):
        bundle_files({"orbit_file": "orbits.x.json.gz", "files": ["meta.json", "raw_gp.csv.gz"]})
    assert "orbits.x.json.gz" in bundle_files({"orbit_file": "orbits.x.json.gz"})


def test_push_bundle_uploads_only_bundle_files_and_prunes(tmp_path, monkeypatch):
    results = tmp_path / "results"
    shutil.copytree(_fixture_run(), results / _fixture_run().name)
    (results / POINTER).write_text(json.dumps({"run_id": _fixture_run().name, "path": _fixture_run().name}))
    (results / _fixture_run().name / "notes.txt").write_text("not part of the bundle")
    calls: dict = {}

    class Op:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class RepositoryNotFoundError(Exception):
        pass

    class FakeApi:
        def __init__(self, token):
            calls["token"] = token

        def repo_info(self, repo_id, **kw):
            if not calls.get("exists"):
                raise RepositoryNotFoundError(repo_id)

        def create_repo(self, repo_id, **kw):
            calls["create"] = kw

        def list_repo_files(self, repo_id, **kw):
            return [f"2026010{i}T000000Z/meta.json" for i in range(1, 9)] + [POINTER]

        def create_commit(self, repo_id, operations, **kw):
            calls["ops"] = operations

    hub = types.SimpleNamespace(HfApi=FakeApi, CommitOperationAdd=Op, CommitOperationDelete=Op)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    push_bundle("someone/perigee-results", "t0ken", results, keep=7)
    added = [o.path_in_repo for o in calls["ops"] if hasattr(o, "path_or_fileobj")]
    deleted = [o.path_in_repo for o in calls["ops"] if not hasattr(o, "path_or_fileobj")]
    assert calls["create"]["private"] is False
    assert POINTER in added and not any("notes.txt" in a for a in added)
    assert all(a == POINTER or a.startswith(_fixture_run().name + "/") for a in added)
    assert len(deleted) == 2  # 8 old runs + the new one, keep 7

    # An existing dataset is used as is (a scoped token may not create repositories).
    calls.clear()
    calls["exists"] = True
    push_bundle("someone/perigee-results", "t0ken", results, keep=7)
    assert "create" not in calls and calls["ops"]


def test_push_bundle_refuses_the_raw_archive(tmp_path):
    with pytest.raises(SystemExit):
        push_bundle("me/perigee-snapshots", "t", tmp_path, archive_repo="me/perigee-snapshots")
