import pandas as pd
import pytest

from src.ingest import archive
from src.storage import parquet_store as store
from tests.conftest import NOW


def test_archive_round_trip_and_reproducible_bytes(tmp_path):
    files = {"stations": b"A,B\n1,2\n"}
    path = archive.write_snapshot(tmp_path, "gp", "20261030T180000Z", files)
    first = (path / "stations.csv.gz").read_bytes()
    assert archive.read_snapshot(tmp_path, "gp", "20261030T180000Z") == files

    # Writing the same bytes again is a no-op; different bytes are refused.
    archive.write_snapshot(tmp_path, "gp", "20261030T180000Z", files)
    assert (path / "stations.csv.gz").read_bytes() == first
    with pytest.raises(archive.ArchiveError):
        archive.write_snapshot(tmp_path, "gp", "20261030T180000Z", {"stations": b"other"})


def test_latest_snapshot_ignores_unfinished_temp_dirs(tmp_path):
    archive.write_snapshot(tmp_path, "gp", "20261030T120000Z", {"a": b"1"})
    archive.write_snapshot(tmp_path, "gp", "20261030T180000Z", {"a": b"2"})
    (tmp_path / "gp" / ".20261031T000000Z.tmp").mkdir()
    assert archive.latest_snapshot(tmp_path, "gp") == "20261030T180000Z"
    assert archive.latest_snapshot(tmp_path, "satcat") is None


def test_snapshot_id_round_trip():
    assert archive.snapshot_time(archive.snapshot_id_for(NOW)) == NOW


def test_parquet_round_trip_keeps_utc(tmp_path):
    df = pd.DataFrame(
        {
            "norad_id": pd.array([25544, 100057], dtype="Int64"),
            "epoch": pd.to_datetime(["2026-10-29T13:31:42.192192", "2026-10-30T01:00:00"], utc=True, format="ISO8601"),
            "name": ["ISS (ZARYA)", None],
        }
    )
    path = store.write_parquet(df, tmp_path / "t" / "t.parquet")
    con = store.connect()
    out = con.execute(f"SELECT * FROM read_parquet('{store.sql_path(path)}')").df()
    assert out["epoch"].iloc[0] == pd.Timestamp("2026-10-29T13:31:42.192192", tz="UTC")
    assert list(out["norad_id"]) == [25544, 100057]
    assert not list(tmp_path.glob("t/.*.tmp"))


def test_latest_catalog_count_reads_newest_snapshot(tmp_path):
    assert store.latest_catalog_count(tmp_path) is None
    for snapshot_id, rows in [("20261029T180000Z", 3), ("20261030T180000Z", 5)]:
        df = pd.DataFrame({"snapshot_id": [snapshot_id] * rows, "norad_id": range(rows)})
        store.write_parquet(df, store.catalog_snapshot_path(tmp_path, snapshot_id))
    assert store.catalog_snapshot_path(tmp_path, "20261030T180000Z").parent.name == (
        "snapshot_date=2026-10-30"
    )
    assert store.latest_catalog_count(tmp_path) == 5
