"""Download the data a deployed site serves, for a code deploy that rebuilds the site
without recomputing anything (.github/workflows/deploy.yml).

Standard library only: the deploy job installs no Python packages, so this module must
not import numpy, pandas or the API (src/results/static_site.py writes these files).

Usage: python -m src.results.site_fetch <site url> <dir>
Exit codes: 0 fetched; 3 the site has no data yet (first deploy: run publish);
anything else is an error and must fail the deploy.
"""

from __future__ import annotations

import json
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

FORMAT = 1  # of index.json; static_site.py writes it
NO_DATA = 3


class NoData(Exception):
    """The site answers but serves no /data/index.json."""


def fetch(site: str, out: Path, timeout: float = 60.0) -> dict:
    """Download every file the site's /data/index.json lists into `out`."""
    base = site.rstrip("/") + "/data/"

    def get(path: str) -> bytes:
        req = urllib.request.Request(base + urllib.parse.quote(path), headers={"User-Agent": "Perigee deploy"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    try:
        raw = get("index.json")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NoData(site) from e
        raise
    try:
        index = json.loads(raw)
    except ValueError as e:
        # Pages answers unknown paths with the app's index.html.
        raise NoData(site) from e
    if index.get("format") != FORMAT:
        raise ValueError(f"the site's data has format {index.get('format')}, this code reads {FORMAT}")
    if out.exists():
        shutil.rmtree(out)
    for path in index["files"]:
        if path.startswith(("/", ".")) or ".." in path.split("/"):
            raise ValueError(f"bad path in the site's index: {path!r}")
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(get(path))
    (out / "index.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
    return index


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: python -m src.results.site_fetch <site url> <dir>")
    try:
        print(f"Fetched run {fetch(sys.argv[1], Path(sys.argv[2]))['run_id']} from {sys.argv[1]}")
    except NoData:
        print(f"{sys.argv[1]} serves no data yet", file=sys.stderr)
        sys.exit(NO_DATA)
