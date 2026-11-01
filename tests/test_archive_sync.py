"""The scheduled job's round trip: pull -> pipeline -> push, with a folder as the dataset."""

from datetime import timedelta

import pytest

from src.ingest import archive
from src.ingest.download import HttpStatusError, PoliteClient
from src.ingest.sources import SATCAT_URL
from src.ops.archive_sync import LocalBackend, PublicDatasetError, pull, push, push_models
from src.pipeline import run_data
from tests.conftest import NOW, STATIONS_URL, FakeFetcher


def _job(settings, fetcher, remote, now):
    """What the GitHub Actions workflow does on a fresh runner."""
    pull(remote, settings.data_dir)
    client = PoliteClient(settings, fetcher=fetcher, sleep=lambda s: None)
    run_data(settings, client=client, now=now, fetch=False)  # baseline for count checks
    outcomes = {o.source: o.status for o in run_data(settings, client=client, now=now)}
    push(remote, settings.data_dir, "test run")
    return outcomes


def _fresh_runner(settings, tmp_path, name):
    return type(settings)(**{**settings.__dict__, "data_dir": tmp_path / name})


def test_first_run_pushes_snapshots_and_state(settings, good_responses, tmp_path):
    remote = LocalBackend(tmp_path / "dataset")
    outcomes = _job(settings, FakeFetcher(good_responses), remote, NOW)

    assert set(outcomes.values()) == {"fetched"}
    files = remote.list_files()
    assert "state/fetch_log.json" in files
    snapshot = archive.snapshot_id_for(NOW)
    assert f"raw/gp/{snapshot}/stations.csv.gz" in files
    assert f"raw/satcat/{snapshot}/satcat.csv.gz" in files
    assert not any(f.startswith("processed/") for f in files)  # rebuilt, never stored


def test_next_runner_honours_intervals_from_pulled_state(settings, good_responses, tmp_path):
    remote = LocalBackend(tmp_path / "dataset")
    fetcher = FakeFetcher(good_responses)
    _job(settings, fetcher, remote, NOW)

    # A brand-new runner an hour later: nothing is due, so no requests at all.
    runner2 = _fresh_runner(settings, tmp_path, "runner2")
    outcomes = _job(runner2, fetcher, remote, NOW + timedelta(hours=1))
    assert set(outcomes.values()) == {"cached"}
    assert len(fetcher.calls) == 3

    # Three hours later only GP is due; its new snapshot is pushed alongside the first.
    runner3 = _fresh_runner(settings, tmp_path, "runner3")
    outcomes = _job(runner3, fetcher, remote, NOW + timedelta(hours=3))
    assert outcomes == {"satcat": "cached", "space_weather": "cached", "gp": "fetched"}
    gp_snapshots = {f.split("/")[2] for f in remote.list_files() if f.startswith("raw/gp/")}
    assert len(gp_snapshots) == 2


def test_count_drop_check_works_across_runners(settings, good_responses, gp_bytes, tmp_path):
    remote = LocalBackend(tmp_path / "dataset")
    fetcher = FakeFetcher(good_responses)
    _job(settings, fetcher, remote, NOW)

    fetcher.responses[STATIONS_URL] = b"".join(gp_bytes.splitlines(keepends=True)[:12])
    runner2 = _fresh_runner(settings, tmp_path, "runner2")
    outcomes = _job(runner2, fetcher, remote, NOW + timedelta(hours=3))
    assert outcomes["gp"] == "rejected"
    assert any(f.startswith("quarantine/gp/") for f in remote.list_files())


def test_stop_file_survives_runners_until_deleted_remotely(settings, good_responses, tmp_path):
    remote = LocalBackend(tmp_path / "dataset")
    fetcher = FakeFetcher(good_responses)
    fetcher.responses[SATCAT_URL] = HttpStatusError(SATCAT_URL, 403)
    _job(settings, fetcher, remote, NOW)
    assert "state/STOP_CELESTRAK" in remote.list_files()

    runner2 = _fresh_runner(settings, tmp_path, "runner2")
    outcomes = _job(runner2, fetcher, remote, NOW + timedelta(days=1))
    assert outcomes["satcat"] == "stopped"
    assert fetcher.calls == [SATCAT_URL]  # no request made by the second runner

    # A human investigates and deletes the STOP file in the dataset.
    (tmp_path / "dataset" / "state" / "STOP_CELESTRAK").unlink()
    fetcher.responses[SATCAT_URL] = good_responses[SATCAT_URL]
    runner3 = _fresh_runner(settings, tmp_path, "runner3")
    outcomes = _job(runner3, fetcher, remote, NOW + timedelta(days=2))
    assert outcomes["satcat"] == "fetched"


def test_push_never_reuploads_existing_snapshots(settings, good_responses, tmp_path):
    remote = LocalBackend(tmp_path / "dataset")
    _job(settings, FakeFetcher(good_responses), remote, NOW)
    uploaded = push(remote, settings.data_dir, "again")
    assert uploaded == ["state/fetch_log.json"]


def test_pull_into_empty_dataset(settings, tmp_path):
    assert pull(LocalBackend(tmp_path / "missing"), settings.data_dir) == {}


def _fake_hf(monkeypatch, *, exists: bool, private: bool):
    hub = pytest.importorskip("huggingface_hub")
    from huggingface_hub.errors import RepositoryNotFoundError

    calls = []

    class FakeApi:
        def __init__(self, token):
            self.exists = exists

        def create_repo(self, repo_id, **kwargs):
            calls.append(("create_repo", kwargs))
            self.exists = True

        def repo_info(self, *args, **kwargs):
            if not self.exists:
                # The real error wants an HTTP response; build it without one.
                error = RepositoryNotFoundError.__new__(RepositoryNotFoundError)
                Exception.__init__(error, "missing")
                raise error
            return type("Info", (), {"private": private})()

    monkeypatch.setattr(hub, "HfApi", FakeApi)
    return calls


def test_hf_backend_refuses_public_dataset(monkeypatch):
    _fake_hf(monkeypatch, exists=True, private=False)
    from src.ops.archive_sync import HfBackend

    with pytest.raises(PublicDatasetError):
        HfBackend("someone/perigee-snapshots", "token")


def test_hf_backend_uses_existing_private_dataset_without_creating(monkeypatch):
    calls = _fake_hf(monkeypatch, exists=True, private=True)
    from src.ops.archive_sync import HfBackend

    HfBackend("someone/perigee-snapshots", "token")
    assert calls == []


def test_hf_backend_creates_missing_dataset_as_private(monkeypatch):
    calls = _fake_hf(monkeypatch, exists=False, private=True)
    from src.ops.archive_sync import HfBackend

    HfBackend("someone/perigee-snapshots", "token")
    assert calls == [("create_repo", {"repo_type": "dataset", "private": True})]


def test_pull_history_days_restores_recent_gp_snapshots_only(settings, tmp_path):
    remote = tmp_path / "remote"
    gp = ["20260901T000000Z", "20261001T000000Z", "20261020T120000Z", "20261031T000000Z"]
    for snap in gp:
        (remote / "raw" / "gp" / snap).mkdir(parents=True)
        (remote / "raw" / "gp" / snap / "active.csv.gz").write_bytes(b"x")
    (remote / "raw" / "satcat" / "20261001T000000Z").mkdir(parents=True)
    (remote / "raw" / "satcat" / "20261001T000000Z" / "satcat.csv.gz").write_bytes(b"x")

    latest_only = _fresh_runner(settings, tmp_path, "latest")
    pull(LocalBackend(remote), latest_only.data_dir)
    assert sorted(p.name for p in (latest_only.data_dir / "raw" / "gp").iterdir()) == ["20261031T000000Z"]

    with_history = _fresh_runner(settings, tmp_path, "history")
    latest = pull(LocalBackend(remote), with_history.data_dir, history_days=30)
    assert latest["gp"] == "20261031T000000Z"
    got = sorted(p.name for p in (with_history.data_dir / "raw" / "gp").iterdir())
    assert got == ["20261001T000000Z", "20261020T120000Z", "20261031T000000Z"]
    assert (with_history.data_dir / "raw" / "satcat" / "20261001T000000Z" / "satcat.csv.gz").exists()


def test_models_round_trip_newest_only(settings, tmp_path):
    remote = tmp_path / "remote"
    laptop = _fresh_runner(settings, tmp_path, "laptop")
    for version in ["20250601-aaaaaaa", "20251225-feaaf72"]:
        d = laptop.data_dir / "models" / version
        d.mkdir(parents=True)
        (d / "lightgbm.txt").write_text(version)
        (d / "results.json").write_text("{}")
    assert len(push_models(LocalBackend(remote), laptop.data_dir)) == 4
    assert push_models(LocalBackend(remote), laptop.data_dir) == []  # nothing new

    runner = _fresh_runner(settings, tmp_path, "runner")
    pull(LocalBackend(remote), runner.data_dir)
    assert not (runner.data_dir / "models").exists()  # the hourly job does not need models
    pull(LocalBackend(remote), runner.data_dir, models=True)
    assert sorted(p.name for p in (runner.data_dir / "models").iterdir()) == ["20251225-feaaf72"]
    assert (runner.data_dir / "models" / "20251225-feaaf72" / "lightgbm.txt").read_text() == "20251225-feaaf72"
