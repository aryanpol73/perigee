"""The static site export (src/results/static_site.py).

Two committed snapshots, refreshed with UPDATE_STATIC_FIXTURE=1 after an intended change:

  tests/fixtures/static/              the fixture bundle exported as static files (the
                                      browser tests and the web app's parity test read it)
  tests/fixtures/static-parity.json   what the API returns for a wide set of requests;
                                      web/src/static-api.test.ts checks the static site
                                      returns the same body for every one
  tests/fixtures/static-parity-simulate.json
                                      POST /v1/simulate answers; web/src/sim-python.test.ts
                                      runs the same simulations in Pyodide and compares
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import replace
from itertools import product
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.data import ResultsStore
from src.config import Settings
from src.results import FIXTURE_DIR
from src.results.static_site import export, shard_of

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "fixtures" / "static"
PARITY = ROOT / "fixtures" / "static-parity.json"
SIM_PARITY = ROOT / "fixtures" / "static-parity-simulate.json"
UPDATE = os.environ.get("UPDATE_STATIC_FIXTURE") == "1"


@pytest.fixture(scope="module")
def store() -> ResultsStore:
    return ResultsStore.load(FIXTURE_DIR)


@pytest.fixture(scope="module")
def client() -> TestClient:
    settings = replace(Settings.from_env(), api_max_data_age_h=float("inf"), api_rate_limit_per_min=10**9)
    return TestClient(create_app(settings, FIXTURE_DIR))


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_export_matches_committed_snapshot(store, tmp_path):
    out = tmp_path / "static"
    export(store, out, float("inf"))
    if UPDATE:
        if STATIC.exists():
            shutil.rmtree(STATIC)
        shutil.copytree(out, STATIC)
    assert _tree(out) == _tree(STATIC), "static export changed: rerun with UPDATE_STATIC_FIXTURE=1 if intended"


def test_export_layout(store, tmp_path):
    index = export(store, tmp_path / "s", 48.0)
    files = _tree(tmp_path / "s")
    assert {"index.json", "meta.json", "conjunctions.json", "objects.json", "shells.json", "sim-catalog.json"} <= set(files)
    assert f"orbits/{index['orbit_file']}" in files and not index["orbit_file"].endswith(".gz")
    assert index["max_data_age_hours"] == 48.0 and index["source"]["snapshot_id"] == store.meta["snapshot_id"]
    # Every approach's extra detail sits in the shard the browser will look in.
    for cid in store.conjunctions.index[:50]:
        shard = json.loads(files[f"conjunction-extra/{shard_of(cid):02d}.json"])
        assert cid in shard and shard[cid]["confidence_note"]


def test_shard_hash_is_fnv1a():
    # Known FNV-1a 32-bit values (the browser computes the same).
    assert shard_of("", 2**32) == 0x811C9DC5
    assert shard_of("a", 2**32) == 0xE40C292C


def _requests(store: ResultsStore) -> list[str]:
    c, o = store.conjunctions, store.objects
    snap = store.snapshot_time
    paths = ["/v1/meta", "/v1/shells", "/v1/shells/history", "/v1/model"]
    times = [None, (snap + (c["tca"].max() - snap) / 3).strftime("%Y-%m-%dT%H:%M:%SZ")]
    for sort, miss, scored, tca_from, page in product(["probability", "tca", "miss"], [None, 1, 2.5], [False, True], times, [(50, 0), (25, 25)]):
        q = [f"sort={sort}", f"limit={page[0]}", f"offset={page[1]}"]
        q += [f"max_miss_km={miss}"] if miss else []
        q += ["scored_only=true"] if scored else []
        q += [f"tca_from={tca_from}"] if tca_from else []
        paths.append("/v1/conjunctions?" + "&".join(q))
    busy = int(c["norad_a"].value_counts().index[0])
    op = store.meta["operating_probability"]
    paths += [
        "/v1/conjunctions", f"/v1/conjunctions?min_probability={op}&limit=500", "/v1/conjunctions?object_type=DEB&limit=100",
        f"/v1/conjunctions?norad_id={busy}&sort=tca", f"/v1/conjunctions?tca_to={times[1]}&sort=miss&limit=500",
        "/v1/conjunctions?limit=500&offset=400", "/v1/conjunctions/does-not-exist",
    ]
    paths += [f"/v1/conjunctions/{cid}" for cid in c.index[::4]]
    names = [str(n) for n in o["name"].dropna().head(3)]
    paths += [f"/v1/objects?q={q}&limit=10" for q in ["ISS", "starlink", "deb", str(int(o.index[5])), *names]]
    paths += ["/v1/objects?object_type=R/B&limit=20", "/v1/objects?min_alt_km=500&max_alt_km=600&offset=10", "/v1/objects/1"]
    no_record = [int(n) for n in o.index if not isinstance(o.loc[n, "object_id"], str)][:5]
    with_approaches = [busy, *[int(n) for n in c["norad_b"].head(10)]]
    paths += [f"/v1/objects/{n}" for n in sorted({*o.index[::40].tolist(), *no_record, *with_approaches})]
    paths.append(f"/v1/orbits/{store.meta['orbit_file']}")
    return paths


def test_api_answers_for_the_parity_check(store, client):
    """Records the API's answers; the web app's test replays them against the static site."""
    cases = []
    for path in _requests(store):
        r = client.get(path)
        cases.append({"path": path, "status": r.status_code, "body": r.json() if r.status_code == 200 else None})
    assert all(case["status"] in (200, 404) for case in cases)
    if UPDATE:
        PARITY.write_text(json.dumps(cases, indent=0, separators=(",", ":")) + "\n", encoding="utf-8")
    assert json.loads(PARITY.read_text(encoding="utf-8")) == cases, "API answers changed: rerun with UPDATE_STATIC_FIXTURE=1 if intended"


SIM_REQUESTS = [
    {"max_points": 200, "times_h": [0.0833, 24, 720]},
    {"altitude_km": 550, "inclination_deg": 53, "target_mass_kg": 260, "impactor_mass_kg": 1400, "impactor_kind": "RB",
     "rel_speed_km_s": 9.8, "seed": 7, "max_points": 150, "times_h": [1, 168, 8760]},
    {"altitude_km": 1200, "rel_speed_km_s": 0.8, "max_points": 100, "times_h": [6]},  # slow: not catastrophic
]


def test_api_simulations_for_the_parity_check(client):
    cases = [{"request": req, "response": client.post("/v1/simulate", json=req).json()} for req in SIM_REQUESTS]
    assert all("frames" in c["response"] for c in cases)
    if UPDATE:
        SIM_PARITY.write_text(json.dumps(cases, separators=(",", ":")) + "\n", encoding="utf-8")
    assert json.loads(SIM_PARITY.read_text(encoding="utf-8")) == cases, "simulations changed: rerun with UPDATE_STATIC_FIXTURE=1"


def test_fetch_downloads_what_the_index_lists(store, tmp_path, monkeypatch):
    import io
    import urllib.request

    from src.results import static_site

    site = tmp_path / "site"
    export(store, site / "data", 48.0)

    def fake_urlopen(req, timeout=0):
        path = req.full_url.split("/data/", 1)[1]
        return io.BytesIO((site / "data" / urllib.parse.unquote(path)).read_bytes())

    import urllib.parse

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    index = static_site.fetch("https://example.test/", tmp_path / "copy")
    assert _tree(tmp_path / "copy") == _tree(site / "data")
    assert index["run_id"] == store.meta["run_id"]
