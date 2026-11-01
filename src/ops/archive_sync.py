"""Keep the raw archive and job state in a private Hugging Face dataset.

The scheduled job runs on a fresh GitHub Actions runner every time, so before
running the pipeline it pulls what it needs, and afterwards it pushes what it
produced:

  pull: state/ (fetch log, STOP file) and the latest raw snapshot of each
        source, so intervals are honoured and validation has a baseline
  push: every raw and quarantined snapshot not yet in the dataset, plus state/

The dataset mirrors the local layout under data/ (raw/, quarantine/, state/).
Raw snapshots are immutable, so push only ever adds files. Processed tables are
not stored: they are rebuilt from raw.

Usage: python -m src.ops.archive_sync pull|push
Env:   HF_TOKEN, PERIGEE_HF_DATASET (owner/name), PERIGEE_DATA_DIR
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Protocol

from src.config import Settings
from src.log import setup_logging

log = logging.getLogger("perigee.ops.archive_sync")

ARCHIVE_DIRS = ("raw", "quarantine")
STATE_DIR = "state"


class Backend(Protocol):
    def list_files(self) -> list[str]: ...
    def download(self, path_in_repo: str, local_path: Path) -> None: ...
    def upload(self, files: dict[str, Path], message: str) -> None: ...


class LocalBackend:
    """A plain directory standing in for the dataset (tests, dry runs)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def list_files(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())

    def download(self, path_in_repo: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.root / path_in_repo, local_path)

    def upload(self, files: dict[str, Path], message: str) -> None:
        for path_in_repo, local_path in files.items():
            target = self.root / path_in_repo
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local_path, target)


class PublicDatasetError(RuntimeError):
    pass


class HfBackend:
    """A private Hugging Face dataset repository."""

    def __init__(self, repo_id: str, token: str) -> None:
        from huggingface_hub import HfApi
        from huggingface_hub.errors import RepositoryNotFoundError

        self.repo_id = repo_id
        self.token = token
        self.api = HfApi(token=token)
        # Look first: a token scoped to just this dataset may not be allowed to
        # call create_repo at all, even for a repo that already exists.
        try:
            info = self.api.repo_info(repo_id, repo_type="dataset")
        except RepositoryNotFoundError:
            self.api.create_repo(repo_id, repo_type="dataset", private=True)
            info = self.api.repo_info(repo_id, repo_type="dataset")
        if not info.private:
            # exist_ok leaves an existing repo's visibility alone; never upload raw data publicly.
            raise PublicDatasetError(
                f"Hugging Face dataset {repo_id} is public. Make it private before running the job."
            )

    def list_files(self) -> list[str]:
        return list(self.api.list_repo_files(self.repo_id, repo_type="dataset"))

    def download(self, path_in_repo: str, local_path: Path) -> None:
        from huggingface_hub import hf_hub_download

        cached = hf_hub_download(
            self.repo_id, path_in_repo, repo_type="dataset", token=self.token
        )
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cached, local_path)

    def upload(self, files: dict[str, Path], message: str) -> None:
        from huggingface_hub import CommitOperationAdd

        operations = [
            CommitOperationAdd(path_in_repo=path, path_or_fileobj=str(local))
            for path, local in sorted(files.items())
        ]
        self.api.create_commit(
            self.repo_id, operations, commit_message=message, repo_type="dataset"
        )


def _snapshot_of(path: str) -> tuple[str, str, str] | None:
    """'raw/gp/20261031T144946Z/active.csv.gz' -> ('raw', 'gp', '20261031T144946Z')."""
    parts = PurePosixPath(path).parts
    if len(parts) == 4 and parts[0] in ARCHIVE_DIRS:
        return parts[0], parts[1], parts[2]
    return None


def _download_snapshot(backend: Backend, remote: list[str], data_dir: Path, source: str, snapshot: str) -> bool:
    """Download one raw snapshot unless it is already here. True if downloaded."""
    target = data_dir / "raw" / source / snapshot
    if target.exists():
        return False
    # Download into a hidden temp dir and rename, so a snapshot directory
    # only ever appears complete (the archive module relies on this).
    tmp = target.with_name(f".{snapshot}.pull")
    if tmp.exists():
        shutil.rmtree(tmp)
    prefix = f"raw/{source}/{snapshot}/"
    for path in remote:
        if path.startswith(prefix):
            backend.download(path, tmp / path.removeprefix(prefix))
    os.replace(tmp, target)
    return True


def _snapshot_time(snapshot: str) -> datetime | None:
    try:
        return datetime.strptime(snapshot, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def pull(backend: Backend, data_dir: Path, history_days: float = 0.0) -> dict[str, str]:
    """Restore state/ and the newest raw snapshot of each source. Returns {source: snapshot}.

    history_days > 0 also restores every GP snapshot from that many days before
    the newest one: publishing needs a month of element sets for the model's
    update-history features, which the hourly job itself does not.
    """
    remote = backend.list_files()

    for path in remote:
        if PurePosixPath(path).parts[0] == STATE_DIR:
            backend.download(path, data_dir / path)

    latest: dict[str, str] = {}
    for path in remote:
        key = _snapshot_of(path)
        if key and key[0] == "raw":
            _, source, snapshot = key
            latest[source] = max(latest.get(source, ""), snapshot)

    for source, snapshot in latest.items():
        _download_snapshot(backend, remote, data_dir, source, snapshot)

    history = 0
    newest = _snapshot_time(latest.get("gp", ""))
    if history_days > 0 and newest is not None:
        since = newest - timedelta(days=history_days)
        gp = sorted({k[2] for p in remote if (k := _snapshot_of(p)) and k[0] == "raw" and k[1] == "gp"})
        for snapshot in gp:
            t = _snapshot_time(snapshot)
            if t is not None and t >= since and _download_snapshot(backend, remote, data_dir, "gp", snapshot):
                history += 1

    log.info("pulled archive state", extra={"latest_snapshots": latest, "history_snapshots": history})
    return latest


def push(backend: Backend, data_dir: Path, message: str) -> list[str]:
    """Upload new raw/quarantined snapshot files and the current state. Returns uploaded paths."""
    remote = set(backend.list_files())
    files: dict[str, Path] = {}

    for top in ARCHIVE_DIRS:
        root = data_dir / top
        if not root.exists():
            continue
        for local in sorted(root.rglob("*.csv.gz")):
            rel = local.relative_to(data_dir).as_posix()
            if any(part.startswith(".") for part in PurePosixPath(rel).parts):
                continue  # unfinished temp directories
            if rel not in remote:
                files[rel] = local

    state = data_dir / STATE_DIR
    if state.exists():
        for local in sorted(p for p in state.iterdir() if p.is_file() and not p.name.endswith(".tmp")):
            files[local.relative_to(data_dir).as_posix()] = local

    if files:
        backend.upload(files, message)
    log.info("pushed archive", extra={"files": len(files)})
    return sorted(files)


def _backend_from_env() -> Backend:
    repo_id = os.environ.get("PERIGEE_HF_DATASET", "").strip()
    token = os.environ.get("HF_TOKEN", "").strip()
    if not repo_id or not token:
        raise SystemExit(
            "Set HF_TOKEN (GitHub secret) and PERIGEE_HF_DATASET (GitHub variable, owner/name)."
        )
    return HfBackend(repo_id, token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="archive_sync")
    parser.add_argument("command", choices=["pull", "push"])
    parser.add_argument(
        "--history-days", type=float, default=0.0, help="pull: also restore GP snapshots from this many days back"
    )
    args = parser.parse_args(argv)

    run_id = setup_logging()
    settings = Settings.from_env()
    backend = _backend_from_env()
    if args.command == "pull":
        latest = pull(backend, settings.data_dir, args.history_days)
        print(f"Pulled latest snapshots: {latest or 'none yet'}")
    else:
        uploaded = push(backend, settings.data_dir, f"Data job run {run_id}")
        print(f"Pushed {len(uploaded)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
