"""End-to-end tests of the data job with HTTP replaced by a fake."""

from datetime import timedelta

from src.ingest import archive
from src.ingest.download import HttpStatusError, NetworkError, PoliteClient
from src.ingest.sources import SATCAT_URL
from src.main import main
from src.pipeline import rebuild_all, run_data
from src.storage import parquet_store as store
from src.storage.summary import catalog_summary, format_summary
from tests.conftest import NOW, STATIONS_URL, FakeFetcher


def _run(settings, fetcher, now=NOW, fetch=True):
    client = PoliteClient(settings, fetcher=fetcher, sleep=lambda s: None)
    outcomes = run_data(settings, client=client, now=now, fetch=fetch)
    return {o.source: o for o in outcomes}


def _status(outcomes):
    return {name: o.status for name, o in outcomes.items()}


def test_first_run_fetches_validates_and_stores(settings, good_responses):
    outcomes = _run(settings, FakeFetcher(good_responses))
    assert _status(outcomes) == {"gp": "fetched", "satcat": "fetched", "space_weather": "fetched"}

    summary = catalog_summary(settings.processed_dir)
    assert summary.total_objects == 20
    assert summary.by_regime == {"LEO": 20}
    assert summary.by_group == {"stations": 20}
    assert summary.satcat_rows == 24
    assert summary.space_weather_last_observed == "2021-01-14"
    assert summary.epoch_age_median_h is not None and summary.epoch_age_median_h > 0
    assert "Objects with orbit data: 20" in format_summary(summary)


def test_run_within_interval_makes_no_requests(settings, good_responses):
    fetcher = FakeFetcher(good_responses)
    _run(settings, fetcher)
    outcomes = _run(settings, fetcher, now=NOW + timedelta(hours=1))
    assert len(fetcher.calls) == 3
    assert set(_status(outcomes).values()) == {"cached"}
    assert catalog_summary(settings.processed_dir).snapshots_stored == 1


def test_each_source_follows_its_own_interval(settings, good_responses):
    fetcher = FakeFetcher(good_responses)
    _run(settings, fetcher)
    outcomes = _run(settings, fetcher, now=NOW + timedelta(hours=3))
    assert _status(outcomes) == {"gp": "fetched", "satcat": "cached", "space_weather": "cached"}
    assert catalog_summary(settings.processed_dir).snapshots_stored == 2


def test_bad_download_is_quarantined_not_stored(settings, good_responses):
    fetcher = FakeFetcher(good_responses)
    first = _run(settings, fetcher)["gp"].snapshot_id

    fetcher.responses[STATIONS_URL] = b"<html>Service temporarily unavailable</html>"
    later = NOW + timedelta(hours=3)
    outcome = _run(settings, fetcher, now=later)["gp"]

    assert outcome.status == "rejected"
    assert archive.list_snapshots(settings.raw_dir, "gp") == [first]
    assert archive.list_snapshots(settings.quarantine_dir, "gp") == [archive.snapshot_id_for(later)]
    summary = catalog_summary(settings.processed_dir)
    assert summary.snapshot_id == first  # last good data stays live
    assert summary.total_objects == 20


def test_partial_download_is_rejected_by_count_drop(settings, good_responses, gp_bytes):
    fetcher = FakeFetcher(good_responses)
    _run(settings, fetcher)

    lines = gp_bytes.splitlines(keepends=True)
    fetcher.responses[STATIONS_URL] = b"".join(lines[:12])  # header + 11 objects
    outcome = _run(settings, fetcher, now=NOW + timedelta(hours=3))["gp"]
    assert outcome.status == "rejected"
    assert "fell from 20 to 11" in outcome.detail


def test_rejected_download_still_counts_against_interval(settings, good_responses):
    fetcher = FakeFetcher(good_responses)
    fetcher.responses[STATIONS_URL] = b"garbage"
    _run(settings, fetcher)
    calls = len(fetcher.calls)
    _run(settings, fetcher, now=NOW + timedelta(minutes=30))
    assert len(fetcher.calls) == calls


def test_http_refusal_halts_all_requests(settings, good_responses):
    fetcher = FakeFetcher(good_responses)
    fetcher.responses[SATCAT_URL] = HttpStatusError(SATCAT_URL, 403)
    outcomes = _run(settings, fetcher)

    assert _status(outcomes) == {"satcat": "refused", "space_weather": "skipped", "gp": "skipped"}
    assert fetcher.calls == [SATCAT_URL]
    assert (settings.state_dir / "STOP_CELESTRAK").exists()

    outcomes = _run(settings, fetcher, now=NOW + timedelta(days=1))
    assert outcomes["satcat"].status == "stopped"
    assert fetcher.calls == [SATCAT_URL]


def test_network_error_is_reported_and_other_sources_continue(settings, good_responses):
    fetcher = FakeFetcher(good_responses)
    fetcher.responses[SATCAT_URL] = NetworkError("connection reset")
    outcomes = _run(settings, fetcher)
    assert _status(outcomes) == {
        "satcat": "network_error",
        "space_weather": "fetched",
        "gp": "fetched",
    }


def test_offline_run_makes_no_requests(settings, good_responses):
    fetcher = FakeFetcher(good_responses)
    outcomes = _run(settings, fetcher, fetch=False)
    assert fetcher.calls == []
    assert set(_status(outcomes).values()) == {"skipped"}


def test_rebuild_from_raw_is_idempotent(settings, good_responses):
    _run(settings, FakeFetcher(good_responses))
    _run(settings, FakeFetcher(good_responses), now=NOW + timedelta(hours=3))

    for _ in range(2):
        assert rebuild_all(settings) == 2
    files = list((settings.processed_dir / store.CATALOG).rglob("*.parquet"))
    assert len(files) == 2
    assert store.latest_catalog_count(settings.processed_dir) == 20


def test_processed_tables_can_be_regenerated_from_raw(settings, good_responses):
    import shutil

    _run(settings, FakeFetcher(good_responses))
    shutil.rmtree(settings.processed_dir)
    rebuild_all(settings)
    assert catalog_summary(settings.processed_dir).total_objects == 20


def test_cli_summary_with_no_data(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PERIGEE_DATA_DIR", str(tmp_path / "empty"))
    assert main(["summary"]) == 0
    assert "No catalog snapshot stored yet" in capsys.readouterr().out
