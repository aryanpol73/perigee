"""Move results bundles between this machine and the public results dataset.

The results dataset holds only derived results (the bundle files the API serves),
never raw archives: raw CelesTrak snapshots and Space-Track history stay in the
private archive dataset (src/ops/archive_sync.py).

  fetch_latest  (API startup, no token): download the newest bundle over HTTPS.
  push_bundle   (publish job, HF_TOKEN): upload one bundle and switch LATEST.json
                in a single commit; prune old runs.

This module imports nothing heavy, so the API image stays small.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("perigee.results.remote")

POINTER = "LATEST.json"
HF_BASE = "https://huggingface.co"
# Every file a bundle may contain. Anything else is refused on upload.
BUNDLE_FILES = {
    "meta.json", "model.json", "objects.parquet", "conjunctions.parquet", "shells.parquet", "shells_history.parquet",
}


def _url(base: str, repo_id: str, path: str) -> str:
    return f"{base.rstrip('/')}/datasets/{repo_id}/resolve/main/{urllib.parse.quote(path)}"


def _get(url: str, timeout: float = 60.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Perigee API (results download)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def bundle_files(meta: dict) -> list[str]:
    files = meta.get("files") or sorted(BUNDLE_FILES | {meta["orbit_file"]})
    unknown = [f for f in files if f not in BUNDLE_FILES and f != meta.get("orbit_file")]
    if unknown or any("/" in f or "\\" in f or f.startswith(".") for f in files):
        raise ValueError(f"unexpected files in bundle manifest: {files}")
    return files


def fetch_latest(repo_id: str, results_dir: Path, base: str = HF_BASE) -> str:
    """Download the newest published bundle into results_dir (if not already here). Returns its run id."""
    pointer = json.loads(_get(_url(base, repo_id, POINTER)))
    run_id = pointer["run_id"]
    path = pointer["path"]
    if "/" in path or "\\" in path or path.startswith("."):
        raise ValueError(f"bad bundle path in pointer: {path!r}")
    target = results_dir / path
    if not (target / "meta.json").exists():
        meta = json.loads(_get(_url(base, repo_id, f"{path}/meta.json")))
        tmp = results_dir / f".{path}.download"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        for name in bundle_files(meta):
            (tmp / name).write_bytes(_get(_url(base, repo_id, f"{path}/{name}")))
        if target.exists():
            shutil.rmtree(target)
        os.replace(tmp, target)
    staged = results_dir / f".{POINTER}.tmp"
    staged.write_text(json.dumps({"run_id": run_id, "path": path}))
    os.replace(staged, results_dir / POINTER)
    log.info("results downloaded", extra={"repo": repo_id, "run_id": run_id})
    return run_id


def push_bundle(repo_id: str, token: str, results_dir: Path, keep: int = 7, archive_repo: str | None = None) -> str:
    """Upload the bundle LATEST.json points at, switch the remote pointer, keep the newest `keep` runs."""
    if archive_repo and repo_id.strip().lower() == archive_repo.strip().lower():
        raise SystemExit("The results dataset must not be the private raw-data archive.")
    from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi

    pointer = json.loads((results_dir / POINTER).read_text())
    path = pointer["path"]
    bundle = results_dir / path
    meta = json.loads((bundle / "meta.json").read_text())
    files = bundle_files(meta)
    api = HfApi(token=token)
    api.create_repo(repo_id, repo_type="dataset", exist_ok=True, private=False)
    existing = api.list_repo_files(repo_id, repo_type="dataset")
    runs = sorted({f.split("/")[0] for f in existing if "/" in f})
    stale = [r for r in runs if r != path][: max(0, len(runs) - keep + 1)] if len(runs) >= keep else []
    ops: list = [CommitOperationAdd(path_in_repo=f"{path}/{name}", path_or_fileobj=str(bundle / name)) for name in files]
    ops.append(CommitOperationAdd(path_in_repo=POINTER, path_or_fileobj=json.dumps(pointer).encode()))
    ops += [CommitOperationDelete(path_in_repo=f"{r}/") for r in stale]
    api.create_commit(repo_id, operations=ops, commit_message=f"Results {meta['run_id']}", repo_type="dataset")
    log.info("results pushed", extra={"repo": repo_id, "run_id": meta["run_id"], "pruned": stale})
    return meta["run_id"]
