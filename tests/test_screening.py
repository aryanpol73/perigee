from itertools import combinations

import numpy as np
import pandas as pd
import pytest

from src.ingest.parse import parse_gp
from src.orbits.propagate import propagate_catalog
from src.screening.screen import (
    ScreenConfig,
    active_mask,
    coarse_candidates,
    encounter_minima,
    screen,
)

START = pd.Timestamp("2026-10-30T00:00:00", tz="UTC")


_MINI: pd.DataFrame | None = None


def _closest_phase(iss: pd.Series, draan: float, dinc: float) -> float:
    """Mean-anomaly offset that brings a tilted ISS copy closest to the ISS in the first 2 h."""
    times = pd.date_range(START, periods=721, freq="10s")

    def min_distance(dm: float) -> float:
        row = iss.copy()
        row["ra_of_asc_node"] = (iss["ra_of_asc_node"] + draan) % 360
        row["inclination"] = iss["inclination"] + dinc
        row["mean_anomaly"] = (iss["mean_anomaly"] + dm) % 360
        r = propagate_catalog(pd.DataFrame([iss, row]), times).position_teme_km
        d2 = np.sum((r[0] - r[1]) ** 2, axis=1)
        k = int(np.clip(np.argmin(d2), 1, len(d2) - 2))
        # Relative motion is nearly straight over +/-10 s, so d^2 is a parabola:
        # its vertex is the true minimum even for 15 km/s crossings.
        y0, y1, y2 = d2[k - 1], d2[k], d2[k + 1]
        curvature = y0 - 2 * y1 + y2
        vertex = y1 - (y0 - y2) ** 2 / (8 * curvature) if curvature > 0 else y1
        return float(np.sqrt(max(vertex, 0.0)))

    grid = np.arange(-180.0, 180.0, 0.5)
    best = grid[int(np.argmin([min_distance(dm) for dm in grid]))]
    fine = np.linspace(best - 0.5, best + 0.5, 201)
    return float(fine[int(np.argmin([min_distance(dm) for dm in fine]))])


def _mini_catalog(gp_bytes: bytes) -> pd.DataFrame:
    """Station objects plus synthetic ISS neighbours solved to pass close by.

    Each synthetic object is the ISS with its orbit plane tilted (RAAN and
    inclination), phased so the two meet near a plane crossing, then nudged
    along-track so the miss distance is not zero. Different tilts give slow
    (tens of m/s) to fast (~15 km/s) encounters.
    """
    global _MINI
    if _MINI is not None:
        return _MINI.copy()
    real = parse_gp({"stations": gp_bytes})
    # A near-circular copy of the ISS orbit as the base: with the real ISS
    # eccentricity, tilted copies cross it at a different radius (km apart) and
    # can never pass close at high crossing angles.
    base = real[real["norad_id"] == 25544].iloc[0].copy()
    base["norad_id"], base["object_name"], base["eccentricity"] = 900_000, "SYNTH-BASE", 1e-5
    synthetic = [base]
    for n, (draan, dinc, nudge) in enumerate(
        [(1.0, 0.0, 0.004), (5.0, 0.0, -0.01), (30.0, 4.0, 0.015), (90.0, 15.0, -0.02), (179.0, 2.0, 0.0)]
    ):
        row = base.copy()
        row["norad_id"] = 900_001 + n
        row["object_name"] = f"SYNTH-{n}"
        row["ra_of_asc_node"] = (base["ra_of_asc_node"] + draan) % 360
        row["inclination"] = base["inclination"] + dinc
        row["mean_anomaly"] = (base["mean_anomaly"] + _closest_phase(base, draan, dinc) + nudge) % 360
        synthetic.append(row)
    _MINI = pd.concat([real, pd.DataFrame(synthetic)], ignore_index=True)
    return _MINI.copy()


def _brute_force(catalog: pd.DataFrame, hours: float, threshold: float) -> pd.DataFrame:
    """Every pair at 1 s steps; each local minimum below threshold, refined by a parabola."""
    times = pd.date_range(START, periods=int(hours * 3600) + 1, freq="1s")
    prop = propagate_catalog(catalog, times)
    r = prop.position_teme_km
    rows = []
    for i, j in combinations(range(len(catalog)), 2):
        d = np.linalg.norm(r[i] - r[j], axis=1)
        k = np.flatnonzero((d[1:-1] < d[:-2]) & (d[1:-1] <= d[2:]) & (d[1:-1] < threshold + 0.5)) + 1
        for kk in k:
            y0, y1, y2 = d[kk - 1] ** 2, d[kk] ** 2, d[kk + 1] ** 2
            shift = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2) if (y0 - 2 * y1 + y2) > 0 else 0.0
            miss = np.sqrt(max(y1 - 0.25 * (y0 - y2) * shift, 0.0))
            if miss < threshold:
                a, b = sorted((int(catalog["norad_id"].iloc[i]), int(catalog["norad_id"].iloc[j])))
                rows.append((a, b, times[kk] + pd.Timedelta(seconds=shift), miss))
    return pd.DataFrame(rows, columns=["norad_a", "norad_b", "tca", "miss_km"])


def test_screen_matches_brute_force(gp_bytes):
    catalog = _mini_catalog(gp_bytes)
    config = ScreenConfig(days=4 / 24, scope="all", workers=1)
    events = screen(catalog, START, config).events
    moving = events[~events["colocated"]]
    reference = _brute_force(catalog, hours=4, threshold=config.threshold_km)
    # Exclude the docked station modules, which brute force sees as one long minimum.
    reference = reference.merge(
        events[events["colocated"]][["norad_a", "norad_b"]], how="left", indicator=True
    ).query("_merge == 'left_only'").drop(columns="_merge")

    assert len(reference) >= 5, "the synthetic objects should create several encounters"
    merged = pd.merge_asof(
        reference.sort_values("tca"),
        moving.sort_values("tca")[["norad_a", "norad_b", "tca", "miss_km"]].rename(columns={"tca": "found_tca", "miss_km": "found_miss"}),
        left_on="tca",
        right_on="found_tca",
        by=["norad_a", "norad_b"],
        tolerance=pd.Timedelta(seconds=2),
        direction="nearest",
    )
    assert merged["found_tca"].notna().all(), merged[merged["found_tca"].isna()]
    assert (merged["tca"] - merged["found_tca"]).abs().max() < pd.Timedelta(milliseconds=200)
    np.testing.assert_allclose(merged["found_miss"], merged["miss_km"], atol=0.02)
    assert len(moving) == len(reference)  # and nothing extra


def test_parallel_workers_give_the_same_events(gp_bytes):
    catalog = _mini_catalog(gp_bytes)
    one = screen(catalog, START, ScreenConfig(days=2 / 24, scope="all", workers=1)).events
    many = screen(catalog, START, ScreenConfig(days=2 / 24, scope="all", workers=2, chunk_steps=37)).events

    def moving(df):
        m = df[~df["colocated"]].sort_values(["norad_a", "norad_b", "tca"]).reset_index(drop=True)
        return m.assign(tca_s=(m["tca"] - START).dt.total_seconds()).drop(columns="tca")

    pd.testing.assert_frame_equal(moving(one), moving(many), check_exact=False, atol=1e-4)
    # Co-located pairs have no meaningful TCA (the distance is flat), so only the pairs must match.
    pairs = lambda df: set(zip(df.loc[df["colocated"], "norad_a"], df.loc[df["colocated"], "norad_b"], strict=False))
    assert pairs(one) == pairs(many)


@pytest.mark.parametrize("chunk_steps", [7, 13, 30, 60])
def test_chunk_boundaries_do_not_create_spurious_events(gp_bytes, chunk_steps):
    """Slow encounters split across chunks must still give exactly the brute-force minima."""
    catalog = _mini_catalog(gp_bytes)
    events = screen(catalog, START, ScreenConfig(days=4 / 24, scope="all", workers=1, chunk_steps=chunk_steps)).events
    moving = events[~events["colocated"]]
    reference = _brute_force(catalog, hours=4, threshold=5.0)
    reference = reference.merge(
        events[events["colocated"]][["norad_a", "norad_b"]], how="left", indicator=True
    ).query("_merge == 'left_only'")
    assert len(moving) == len(reference)


def test_docked_modules_are_flagged_colocated_once(gp_bytes):
    catalog = _mini_catalog(gp_bytes)
    events = screen(catalog, START, ScreenConfig(days=2 / 24, scope="all", workers=1)).events
    co = events[events["colocated"]]
    # ISS (ZARYA) 25544, POISK 36086 and NAUKA 49044 share one element set.
    assert {(25544, 36086), (25544, 49044), (36086, 49044)} <= set(zip(co["norad_a"], co["norad_b"], strict=False))
    assert not co.duplicated(["norad_a", "norad_b"]).any()


def test_active_scope_skips_pairs_without_an_active_satellite(gp_bytes):
    catalog = _mini_catalog(gp_bytes)
    nobody = np.zeros(len(catalog), dtype=bool)
    assert screen(catalog, START, ScreenConfig(days=1 / 24, workers=1), nobody).events.empty
    only_synth = catalog["object_name"].str.startswith("SYNTH").to_numpy()
    events = screen(catalog, START, ScreenConfig(days=4 / 24, workers=1), only_synth).events
    ids = set(catalog.loc[only_synth, "norad_id"])
    assert len(events) and (events["norad_a"].isin(ids) | events["norad_b"].isin(ids)).all()


def test_events_carry_identities_and_data_age(gp_bytes):
    events = screen(_mini_catalog(gp_bytes), START, ScreenConfig(days=4 / 24, scope="all", workers=1)).events
    assert (events["norad_a"] < events["norad_b"]).all()
    assert events["norad_a"].dtype == np.int64
    assert ((events["tca"] >= START) & (events["tca"] <= START + pd.Timedelta(hours=4))).all()
    assert (events["miss_km"] < 5.0).all()
    # Days since epoch = TCA - each object's own epoch.
    epochs = _mini_catalog(gp_bytes).set_index("norad_id")["epoch"]
    for side in ("a", "b"):
        expected = (events["tca"] - events[f"norad_{side}"].map(epochs)) / pd.Timedelta(days=1)
        np.testing.assert_allclose(events[f"dse_{side}"], expected, atol=1e-9)


def test_coarse_candidates_straight_line_geometry():
    # Two objects 100 km apart, closing head-on at 14 km/s with a 2 km offset:
    # closest approach in ~7.1 s at 2 km.
    p = np.array([[7000.0, 0.0, 0.0], [7000.0, 100.0, 2.0]])
    v = np.array([[0.0, 7.0, 0.0], [0.0, -7.0, 0.0]])
    cfg = ScreenConfig(step_s=20.0)
    i, j, miss, tau = coarse_candidates(p, v, np.array([True, True]), np.array([True, False]), cfg)
    assert (i.tolist(), j.tolist()) == ([0], [1])
    assert miss[0] == pytest.approx(2.0, abs=1e-9)
    assert tau[0] == pytest.approx(100 / 14, abs=1e-9)


def test_coarse_candidates_respect_usable_and_threshold():
    p = np.array([[7000.0, 0.0, 0.0], [7000.0, 0.0, 30.0], [7000.0, 1.0, 0.0]])
    v = np.array([[0.0, 7.5, 0.0], [0.0, 7.5, 0.0], [0.0, 7.5, 0.0]])  # flying together
    cfg = ScreenConfig(step_s=20.0)
    i, _j, _, _ = coarse_candidates(p, v, np.array([True, True, False]), np.ones(3, bool), cfg)
    assert len(i) == 0  # 30 km apart and parallel; the third object did not propagate


def test_encounter_minima_keeps_one_per_run():
    i = np.array([0, 0, 0, 0, 0, 1])
    j = np.array([1, 1, 1, 1, 1, 2])
    step = np.array([10, 11, 12, 30, 31, 11])
    miss = np.array([4.0, 1.0, 3.0, 2.0, 0.5, 4.5])
    picked = sorted(encounter_minima(i, j, step, miss).tolist())
    assert picked == [1, 4, 5]


def test_run_screen_stores_list_and_summary(settings, good_responses):
    import json

    from src.ingest.download import PoliteClient
    from src.pipeline import run_data
    from src.screening.run import run_screen
    from src.storage import parquet_store as store
    from tests.conftest import NOW, FakeFetcher

    run_data(settings, client=PoliteClient(settings, fetcher=FakeFetcher(good_responses), sleep=lambda s: None), now=NOW)
    result = run_screen(settings, ScreenConfig(days=1 / 24, workers=1))

    path = store.conjunctions_path(settings.processed_dir, "20261030T180000Z", result.config.scope)
    stored = store.connect().execute(f"SELECT * FROM read_parquet('{store.sql_path(path)}')").df()
    assert len(stored) == len(result.events) > 0  # the docked ISS vehicles at least
    assert set(stored["run_id"]) == {f"20261030T180000Z_{result.config.scope}"}
    summary = json.loads(path.with_suffix(".json").read_text())
    assert summary["objects"] == 20 and summary["events"] == len(stored)


def test_active_mask_from_satcat_status():
    catalog = pd.DataFrame({"norad_id": [1, 2, 3, 4]})
    satcat = pd.DataFrame({"norad_id": [1, 2, 3], "ops_status_code": ["+", "-", "P"]})
    assert active_mask(catalog, satcat).tolist() == [True, False, True, False]
    assert active_mask(catalog, None).tolist() == [False] * 4
