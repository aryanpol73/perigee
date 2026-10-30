"""Immutable archive of raw downloads.

Layout: <root>/<source>/<snapshot_id>/<stem>.csv.gz

A snapshot directory appears atomically (written to a temp dir, then renamed),
so its existence means every file in it is complete. Raw files are never
modified; every processed table can be rebuilt from them.
"""

from __future__ import annotations

import gzip
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

SNAPSHOT_FORMAT = "%Y%m%dT%H%M%SZ"


def snapshot_id_for(when: datetime) -> str:
    return when.astimezone(UTC).strftime(SNAPSHOT_FORMAT)


def snapshot_time(snapshot_id: str) -> datetime:
    return datetime.strptime(snapshot_id, SNAPSHOT_FORMAT).replace(tzinfo=UTC)


class ArchiveError(RuntimeError):
    pass


def write_snapshot(root: Path, source: str, snapshot_id: str, files: dict[str, bytes]) -> Path:
    target = root / source / snapshot_id
    if target.exists():
        if read_snapshot(root, source, snapshot_id) == files:
            return target  # same bytes already archived: idempotent no-op
        raise ArchiveError(f"refusing to overwrite raw snapshot {target}")
    tmp = target.with_name(f".{snapshot_id}.tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    for stem, data in files.items():
        # mtime=0 makes the archive byte-for-byte reproducible.
        (tmp / f"{stem}.csv.gz").write_bytes(gzip.compress(data, mtime=0))
    os.replace(tmp, target)
    return target


def read_snapshot(root: Path, source: str, snapshot_id: str) -> dict[str, bytes]:
    folder = root / source / snapshot_id
    return {
        path.name.removesuffix(".csv.gz"): gzip.decompress(path.read_bytes())
        for path in sorted(folder.glob("*.csv.gz"))
    }


def list_snapshots(root: Path, source: str) -> list[str]:
    folder = root / source
    if not folder.exists():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_dir() and not p.name.startswith("."))


def latest_snapshot(root: Path, source: str) -> str | None:
    snapshots = list_snapshots(root, source)
    return snapshots[-1] if snapshots else None
