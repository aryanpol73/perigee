"""API contract tests on the committed fixture bundle (build guide 10.7), plus the bundle and scoring code."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.config import Settings
from src.ml.features import FEATURES
from src.ml.score import Model, score_events
from src.results import FIXTURE_DIR
from src.results.bundle import POINTER, InvalidBundle, publish, validate

OPENAPI_SNAPSHOT = Path(__file__).parent / "fixtures" / "openapi.json"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app(Settings(api_max_data_age_h=1e9), FIXTURE_DIR))


def fixture_bundle() -> Path:
    return FIXTURE_DIR / json.loads((FIXTURE_DIR / POINTER).read_text())["path"]


# --- Contract ---------------------------------------------------------------------


def test_openapi_schema_matches_snapshot(client):
    schema = client.get("/openapi.json").json()
    if os.environ.get("UPDATE_OPENAPI_SNAPSHOT") == "1" or not OPENAPI_SNAPSHOT.exists():
        OPENAPI_SNAPSHOT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert schema == json.loads(OPENAPI_SNAPSHOT.read_text(encoding="utf-8")), (
        "API schema changed. If intended, rerun with UPDATE_OPENAPI_SNAPSHOT=1 and commit the snapshot."
    )


def test_no_collision_probability_wording(client):
    text = json.dumps(client.get("/openapi.json").json()).lower()
    assert "collision_probability" not in text and "risk_probability_estimate" in text


def test_health_and_meta(client):
    h = client.get("/v1/health")
    assert h.status_code == 200 and h.json()["status"] == "ok"
    assert client.get("/v1/conjunctions", params={"limit": 1}).json()["source"]["history_complete"] is False
    m = client.get("/v1/meta").json()
    assert m["object_count"] > 100 and m["disclaimer"] and m["orbit_file_url"].startswith("/v1/orbits/orbits.")
    assert "Space-Track" in m["sources"]


def test_stale_data_makes_health_fail():
    stale = TestClient(create_app(Settings(api_max_data_age_h=0.001), FIXTURE_DIR))
    r = stale.get("/v1/health")
    assert r.status_code == 503 and r.json()["status"] == "stale"


def test_fixture_mode_health_has_no_age_limit():
    unlimited = TestClient(create_app(Settings(api_max_data_age_h=float("inf")), FIXTURE_DIR))
    r = unlimited.get("/v1/health")
    assert r.status_code == 200 and r.json()["max_data_age_hours"] is None


def test_no_results_published(tmp_path):
    empty = TestClient(create_app(Settings(), tmp_path))
    assert empty.get("/v1/health").status_code == 503
    r = empty.get("/v1/conjunctions")
    assert r.status_code == 503 and r.json()["error"]["code"] == "unavailable"


def test_orbit_file_is_precompressed_and_cacheable(client):
    url = client.get("/v1/meta").json()["orbit_file_url"]
    r = client.get(url)
    assert r.status_code == 200 and r.headers["content-encoding"] == "gzip"
    assert "immutable" in r.headers["cache-control"]
    body = r.json()
    assert body["fields"][0] == "NORAD_CAT_ID" and len(body["rows"]) == client.get("/v1/meta").json()["object_count"]
    assert client.get("/v1/orbits/orbits.deadbeef.json.gz").status_code == 404


def test_objects_filters_and_detail(client):
    r = client.get("/v1/objects", params={"q": "ISS", "limit": 5}).json()
    assert any(o["norad_id"] == 25544 for o in r["items"])
    deb = client.get("/v1/objects", params={"object_type": "DEB", "limit": 500}).json()
    assert all(o["object_type"] == "DEB" for o in deb["items"])
    low = client.get("/v1/objects", params={"max_alt_km": 500, "limit": 500}).json()
    assert all(o["perigee_km"] <= 500 for o in low["items"])
    d = client.get("/v1/objects/25544").json()
    assert d["name"].startswith("ISS") and d["source"]["snapshot_id"]
    assert client.get("/v1/objects/999999999").status_code == 404


def test_conjunctions_sorted_filtered_and_paginated(client):
    page = client.get("/v1/conjunctions", params={"limit": 20}).json()
    probs = [c["risk_probability_estimate"] for c in page["items"] if c["risk_probability_estimate"] is not None]
    assert probs == sorted(probs, reverse=True) and all(0 <= p <= 1 for p in probs)
    assert page["disclaimer"] and page["total"] >= len(page["items"])
    by_tca = client.get("/v1/conjunctions", params={"sort": "tca", "limit": 50}).json()["items"]
    assert [c["tca"] for c in by_tca] == sorted(c["tca"] for c in by_tca)
    near = client.get("/v1/conjunctions", params={"max_miss_km": 1, "limit": 500}).json()["items"]
    assert all(c["miss_distance_km"] <= 1 for c in near)
    second = client.get("/v1/conjunctions", params={"limit": 5, "offset": 5}).json()["items"]
    first = client.get("/v1/conjunctions", params={"limit": 10}).json()["items"]
    assert [c["id"] for c in second] == [c["id"] for c in first[5:10]]


def test_out_of_scope_rows_have_no_estimate(client):
    items = client.get("/v1/conjunctions", params={"sort": "tca", "limit": 500, "offset": 0}).json()["items"]
    for c in items:
        if not c["in_model_scope"]:
            assert c["risk_probability_estimate"] is None and c["above_operating_point"] is None
            assert c["estimate_status"] == "none"
        elif c["risk_probability_estimate"] is not None:
            # The fixture has no update history, so every estimate is provisional.
            assert c["estimate_status"] == "provisional"


def test_empty_result_is_not_an_error(client):
    r = client.get("/v1/conjunctions", params={"tca_from": "2100-01-01T00:00:00Z"})
    assert r.status_code == 200 and r.json()["items"] == [] and r.json()["total"] == 0


@pytest.mark.parametrize(
    "params",
    [{"limit": 10_000}, {"limit": 0}, {"sort": "bogus"}, {"min_probability": 2}, {"tca_from": "yesterday"}],
)
def test_malformed_filters_are_rejected(client, params):
    r = client.get("/v1/conjunctions", params=params)
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "invalid_request" and body["request_id"] and "Traceback" not in body["message"]


def test_conjunction_detail(client):
    cid = client.get("/v1/conjunctions", params={"scored_only": True, "limit": 1}).json()["items"][0]["id"]
    d = client.get(f"/v1/conjunctions/{cid}").json()
    assert len(d["object_details"]) == 2 and d["confidence_note"] and d["top_reasons"]
    assert client.get("/v1/conjunctions/not-an-id").status_code == 404


def test_shells_model_and_history(client):
    bands = client.get("/v1/shells").json()["bands"]
    assert len(bands) == 72 and max(b["risk_index"] for b in bands) == pytest.approx(100)
    assert client.get("/v1/shells/history").json()["runs"]
    m = client.get("/v1/model").json()
    assert m["metrics"]["lightgbm"]["pr_auc"] > m["metrics"]["c_logistic_regression"]["pr_auc"]


def test_etag_gives_not_modified(client):
    first = client.get("/v1/shells")
    again = client.get("/v1/shells", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304


def test_cors_allows_only_configured_origin(client):
    ok = client.get("/v1/meta", headers={"Origin": "http://localhost:5173"})
    bad = client.get("/v1/meta", headers={"Origin": "https://evil.example"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in bad.headers


def test_rate_limit():
    limited = TestClient(create_app(Settings(api_rate_limit_per_min=3), FIXTURE_DIR))
    codes = [limited.get("/v1/meta").status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[-1] == 429
    assert limited.get("/v1/health").status_code in (200, 503)  # health is never rate limited


def test_simulate(client):
    r = client.post("/v1/simulate", json={"max_points": 200, "times_h": [0.08, 24, 8760]})
    assert r.status_code == 200
    body = r.json()
    assert body["label"].startswith("Illustrative") and body["catastrophic"]
    assert [f["hours"] for f in body["frames"]] == [0.08, 24, 8760]
    assert all(len(f["points"]) == body["points_returned"] <= 200 for f in body["frames"])
    lat, lon, alt = body["frames"][0]["points"][0]
    assert -90 <= lat <= 90 and -180 <= lon <= 180 and 300 < alt < 1_500
    assert body["frames"][1]["along_track_coverage"] > 0.9
    again = client.post("/v1/simulate", json={"max_points": 200, "times_h": [0.08, 24, 8760]}).json()
    assert again["frames"][0]["points"] == body["frames"][0]["points"]  # seeded


@pytest.mark.parametrize(
    "body",
    [{"altitude_km": 50}, {"target_mass_kg": 1e6}, {"max_points": 5_000}, {"times_h": [1e6]}, {"times_h": []}, {"extra": 1}],
)
def test_simulate_rejects_out_of_bounds(client, body):
    assert client.post("/v1/simulate", json=body).status_code == 422


# --- Bundle publishing ---------------------------------------------------------------


def test_fixture_bundle_is_valid():
    meta = validate(fixture_bundle(), min_objects=100)
    assert meta["disclaimer"]


def test_broken_bundle_is_rejected_and_pointer_kept(tmp_path):
    staged = tmp_path / "good.tmp"
    shutil.copytree(fixture_bundle(), staged)
    publish(staged, tmp_path, "good", min_objects=100)
    broken = tmp_path / "broken.tmp"
    shutil.copytree(tmp_path / "good", broken)
    for f in broken.glob("orbits.*"):
        f.unlink()
    with pytest.raises(InvalidBundle, match="orbit file"):
        publish(broken, tmp_path, "broken", min_objects=100)
    assert json.loads((tmp_path / POINTER).read_text())["run_id"] == "good"
    with pytest.raises(InvalidBundle, match="objects"):
        validate(tmp_path / "good", min_objects=10**6)


# --- Scoring ----------------------------------------------------------------------------


def test_scoring_scope_and_probabilities():
    rng = np.random.default_rng(0)
    x = pd.DataFrame(rng.normal(size=(300, len(FEATURES))), columns=FEATURES)
    booster = lgb.train({"objective": "binary", "verbosity": -1}, lgb.Dataset(x, (x["miss_km"] < 0).astype(int)), 5)
    model = Model("test", booster, np.array([0.0, 1.0]), np.array([0.0, 1.0]),
                  {"models": {"lightgbm": {"operating_threshold": 0.5}}})
    start = pd.Timestamp("2026-01-01", tz="UTC")
    events = pd.DataFrame(
        {
            "norad_a": [1, 1, 1], "norad_b": [2, 2, 2],
            "tca": [start + pd.Timedelta(hours=h) for h in (10, 100, 20)],
            "miss_km": [1.0, 1.0, 1.0], "rel_speed_km_s": [10.0] * 3, "dse_a": [0.5] * 3, "dse_b": [0.5] * 3,
            "colocated": [False, False, False], "same_family": [False, False, True],
        }
    )
    catalog = pd.DataFrame(
        {
            "norad_id": [1, 2], "epoch": [start - pd.Timedelta(hours=6)] * 2, "mean_motion": [15.2, 14.8],
            "eccentricity": [0.001, 0.002], "inclination": [53.0, 97.0], "ra_of_asc_node": [10.0, 200.0],
            "arg_of_pericenter": [0.0, 90.0], "mean_anomaly": [0.0, 180.0], "bstar": [1e-4, 2e-4],
            "mean_motion_dot": [0.0, 0.0], "mean_motion_ddot": [0.0, 0.0],
        }
    )
    stats = pd.DataFrame(columns=["norad_id", "n_records", "sma_jump_max_km", "sma_jump_median_km", "update_gap_median_h"])
    out = score_events(events, catalog, None, stats, None, start, model, explain=True)
    assert out["in_model_scope"].tolist() == [True, False, False]
    assert 0 <= out.loc[0, "risk_probability_estimate"] <= 1
    assert out.loc[1:, "risk_probability_estimate"].isna().all()
    assert len(json.loads(out.loc[0, "top_reasons"])) == 3


def test_cached_responses_are_identical_with_and_without_gzip(client):
    url = "/v1/conjunctions?limit=40&sort=miss"
    plain = client.get(url, headers={"Accept-Encoding": "identity"})
    zipped = client.get(url, headers={"Accept-Encoding": "gzip"})
    again = client.get(url, headers={"Accept-Encoding": "identity"})
    assert zipped.headers.get("content-encoding") == "gzip" and "content-encoding" not in again.headers
    assert plain.json() == zipped.json() == again.json()
    misses = [c["miss_distance_km"] for c in again.json()["items"]]
    assert misses == sorted(misses)
