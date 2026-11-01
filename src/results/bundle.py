"""Build, validate and publish the results bundle the API serves (build guide 4, 10.3, 13.7).

A bundle is one directory per run under results_dir:
  meta.json            snapshot id, generated-at, counts, model version, commit, disclaimer
  objects.parquet      one row per object (identity + orbit summary)
  conjunctions.parquet scored close approaches (same-constellation pairs left out, counted in meta)
  shells.parquet       risk index per altitude band for this snapshot
  shells_history.parquet  every stored shell-index run, for the trend chart
  model.json           model card summary (or null when no model is trained)
  orbits.<hash>.json.gz  compact orbit records for satellite.js in the browser

It is written to a temporary directory, validated, renamed into place, and only
then does LATEST.json switch to it, so the API never reads a half-written run.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import Settings
from src.ml.pipeline import git_commit, load_space_weather
from src.ml.score import FULL_HISTORY_DAYS, MODEL_HORIZON_H, Model, load_model, score_events
from src.risk.shells import prepare_objects, shell_index
from src.screening.run import latest_catalog
from src.screening.socrates import family
from src.storage import parquet_store as store
from src.storage.elsets import ElsetHistory

log = logging.getLogger("perigee.results")

DISCLAIMER = "Estimates from public orbit data; not for operational use."
POINTER = "LATEST.json"
# Fields satellite.js's json2satrec reads from an OMM record, plus identity.
ORBIT_FIELDS = (
    "NORAD_CAT_ID", "OBJECT_NAME", "OBJECT_TYPE", "EPOCH", "MEAN_MOTION", "ECCENTRICITY", "INCLINATION",
    "RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY", "BSTAR", "MEAN_MOTION_DOT", "MEAN_MOTION_DDOT",
    "EPHEMERIS_TYPE", "CLASSIFICATION_TYPE", "ELEMENT_SET_NO", "REV_AT_EPOCH",
)


class InvalidBundle(RuntimeError):
    pass


@dataclass
class Bundle:
    path: Path
    meta: dict


def _satcat_info(settings: Settings) -> pd.DataFrame:
    path = store.table_path(settings.processed_dir, store.SATCAT)
    if not path.exists():
        return pd.DataFrame(columns=["norad_id", "object_type", "rcs_size", "owner", "launch_date"])
    con = store.connect()
    try:
        return con.execute(
            f"SELECT norad_id, object_name AS satcat_name, object_type, ops_status_code, rcs_size, owner, launch_date "
            f"FROM {store.table_relation(settings.processed_dir, store.SATCAT)}"
        ).df().drop_duplicates("norad_id")
    finally:
        con.close()


def _objects(catalog: pd.DataFrame, satcat: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame:
    info = satcat.set_index("norad_id")
    out = pd.DataFrame(
        {
            "norad_id": catalog["norad_id"].astype("int64"),
            "name": catalog["object_name"].fillna(catalog["norad_id"].map(info.get("satcat_name", pd.Series(dtype=str)))),
            "object_id": catalog["object_id"],
            "epoch": catalog["epoch"],
            "epoch_age_h": (start - catalog["epoch"]) / pd.Timedelta(hours=1),
            "perigee_km": catalog["perigee_km"],
            "apogee_km": catalog["apogee_km"],
            "inclination_deg": catalog["inclination"],
            "period_min": 1_440.0 / catalog["mean_motion"],
            "eccentricity": catalog["eccentricity"],
            "regime": catalog["regime"],
        }
    )
    for col in ("object_type", "rcs_size", "owner", "ops_status_code", "launch_date"):
        out[col] = out["norad_id"].map(info[col]) if col in info else None
    out["object_type"] = out["object_type"].fillna("UNK")
    return out.reset_index(drop=True)


def _conjunctions(settings: Settings, snapshot_id: str, objects: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    path = store.conjunctions_path(settings.processed_dir, snapshot_id, "active")
    if not path.exists():
        raise SystemExit(f"No conjunction screen for snapshot {snapshot_id}. Run: python -m src.main screen")
    con = store.connect()
    try:
        events = con.execute(f"SELECT * FROM read_parquet('{store.sql_path(path)}')").df()
    finally:
        con.close()
    names = objects.set_index("norad_id")["name"]
    events = events[~events["colocated"]].copy()
    fam_a, fam_b = events["norad_a"].map(names).map(family), events["norad_b"].map(names).map(family)
    events["same_family"] = (fam_a == fam_b) & fam_a.notna()
    counts = {
        "screened": int(len(events)),
        "same_constellation_left_out": int(events["same_family"].sum()),
    }
    return events[~events["same_family"]].reset_index(drop=True), counts


def _score(settings: Settings, events: pd.DataFrame, catalog: pd.DataFrame, satcat: pd.DataFrame, start: pd.Timestamp,
           model: Model | None) -> tuple[pd.DataFrame, float]:
    if model is None:
        out = events.assign(in_model_scope=(events["tca"] - start) / pd.Timedelta(hours=1) <= MODEL_HORIZON_H)
        return out.assign(raw_score=np.nan, risk_probability_estimate=np.nan, top_reasons=None), float("nan")
    history = ElsetHistory(settings.processed_dir)
    try:
        stats, coverage = history.update_stats(start), history.coverage_days(start)
    finally:
        history.con.close()
    scored = score_events(events, catalog, satcat, stats, load_space_weather(settings), start, model, explain=True)
    return scored, coverage


def _orbit_file(catalog: pd.DataFrame, objects: pd.DataFrame, out_dir: Path) -> str:
    kinds = objects.set_index("norad_id")["object_type"]
    rows = [
        [
            int(r.norad_id), r.object_name, kinds.get(int(r.norad_id), "UNK"),
            pd.Timestamp(r.epoch).strftime("%Y-%m-%dT%H:%M:%S.%f"), float(r.mean_motion), float(r.eccentricity),
            float(r.inclination), float(r.ra_of_asc_node), float(r.arg_of_pericenter), float(r.mean_anomaly),
            float(r.bstar), float(r.mean_motion_dot), float(r.mean_motion_ddot), int(r.ephemeris_type or 0),
            r.classification_type or "U", int(r.element_set_no or 999), int(r.rev_at_epoch or 0),
        ]
        for r in catalog.itertuples(index=False)
    ]
    raw = json.dumps({"fields": list(ORBIT_FIELDS), "rows": rows}, separators=(",", ":")).encode()
    name = f"orbits.{hashlib.sha256(raw).hexdigest()[:12]}.json.gz"
    (out_dir / name).write_bytes(gzip.compress(raw, mtime=0))
    return name


def _model_summary(model: Model | None) -> dict | None:
    if model is None:
        return None
    r = model.results
    lgbm = r["models"]["lightgbm"]
    return {
        "model_version": model.version,
        "commit": r.get("commit"),
        "data_window": r.get("data_window"),
        "label": f"refreshed miss distance below {r['dataset_config']['label_km']:g} km with newer public data",
        "scope": f"approaches predicted up to {MODEL_HORIZON_H:g} h ahead; same-constellation pairs excluded",
        "test_counts": r["counts"]["test"],
        "metrics": {
            k: {"pr_auc": m["pr_auc"], "pr_auc_ci95": m["pr_auc_ci95"], **m["at_operating_point"], "brier": m.get("brier")}
            for k, m in r["models"].items()
        },
        "tradeoff": lgbm.get("tradeoff"),
        "operating_probability": model.operating_threshold,
        "lightgbm_beats_all_baselines": r["lightgbm_beats_all_baselines"],
        "top_features": r["mean_abs_shap"],
        "limits": [
            "Not for operational decisions: public orbit data has kilometre-level error and no covariance.",
            "Manoeuvres between prediction and closest approach make the label describe a different trajectory.",
            "Same-constellation pairs are outside the model's scope.",
        ],
    }


def _shell_history(settings: Settings) -> pd.DataFrame:
    folder = settings.processed_dir / store.SHELL_RISK
    files = sorted(folder.glob("*.parquet")) if folder.exists() else []
    if not files:
        return pd.DataFrame(columns=["run_id", "source", "alt_lo_km", "alt_hi_km", "risk_index", "risk_index_weighted"])
    con = store.connect()
    try:
        glob = store.sql_path(folder) + "/*.parquet"
        return con.execute(
            f"SELECT run_id, source, alt_lo_km, alt_hi_km, objects, risk_index, risk_index_weighted "
            f"FROM read_parquet('{glob}', union_by_name = true) ORDER BY run_id, alt_lo_km"
        ).df()
    finally:
        con.close()


def validate(path: Path, min_objects: int) -> dict:
    """Refuse a bundle that is incomplete or inconsistent; returns its meta."""
    meta = json.loads((path / "meta.json").read_text())
    con = store.connect()
    try:
        def read(name: str) -> pd.DataFrame:
            return con.execute(f"SELECT * FROM read_parquet('{store.sql_path(path / name)}')").df()

        objects, conj, shells = read("objects.parquet"), read("conjunctions.parquet"), read("shells.parquet")
    finally:
        con.close()
    problems = []
    if len(objects) < min_objects:
        problems.append(f"{len(objects)} objects < {min_objects}")
    if objects["norad_id"].duplicated().any():
        problems.append("duplicate norad_id in objects")
    if conj["id"].duplicated().any():
        problems.append("duplicate conjunction id")
    p = conj["risk_probability_estimate"].dropna()
    if ((p < 0) | (p > 1)).any():
        problems.append("probability outside [0, 1]")
    if not set(conj["norad_a"]).union(conj["norad_b"]).issubset(set(objects["norad_id"])):
        problems.append("conjunction refers to an unknown object")
    if shells.empty or (shells["risk_index"] < 0).any():
        problems.append("shell index missing or negative")
    orbit = path / meta["orbit_file"]
    if not orbit.exists():
        problems.append("orbit file missing")
    else:
        rows = json.loads(gzip.decompress(orbit.read_bytes()))["rows"]
        if len(rows) != len(objects):
            problems.append(f"orbit file has {len(rows)} rows, objects {len(objects)}")
    if problems:
        raise InvalidBundle("; ".join(problems))
    return meta


def publish(tmp: Path, results_dir: Path, run_id: str, min_objects: int) -> Bundle:
    """Validate a staged bundle, move it into place, then switch the pointer atomically."""
    meta = validate(tmp, min_objects)
    final = results_dir / run_id
    if final.exists():
        shutil.rmtree(final)
    os.replace(tmp, final)
    pointer = results_dir / f".{POINTER}.tmp"
    pointer.write_text(json.dumps({"run_id": run_id, "path": run_id}))
    os.replace(pointer, results_dir / POINTER)
    return Bundle(final, meta)


def build(settings: Settings, model_version: str | None = None, select: pd.Series | None = None,
          results_dir: Path | None = None, run_suffix: str = "", min_objects: int | None = None) -> Bundle:
    """Build and publish a bundle for the latest snapshot.

    select: optional boolean mask over the snapshot's objects (a fixture keeps a
    small subset; its conjunctions are those between kept objects).
    """
    results_dir = results_dir or settings.results_dir
    catalog = latest_catalog(settings)
    snapshot_id = str(catalog["snapshot_id"].iloc[0])
    start = pd.Timestamp(catalog["snapshot_time"].iloc[0])
    satcat = _satcat_info(settings)
    model = load_model(settings.data_dir / "models", model_version)

    objects_all = _objects(catalog, satcat, start)
    events, counts = _conjunctions(settings, snapshot_id, objects_all)
    shells = shell_index(prepare_objects(catalog, satcat, "default"))
    if select is not None:
        keep = set(catalog.loc[select.to_numpy(), "norad_id"])
        catalog = catalog[catalog["norad_id"].isin(keep)].reset_index(drop=True)
        events = events[events["norad_a"].isin(keep) & events["norad_b"].isin(keep)].reset_index(drop=True)
    objects = _objects(catalog, satcat, start)
    scored, coverage = _score(settings, events, catalog, satcat, start, model)

    by_id = objects.set_index("norad_id")
    hours = (scored["tca"] - start) / pd.Timedelta(hours=1)
    conj = pd.DataFrame(
        {
            "id": [f"{snapshot_id}_{a}_{b}_{int(t.timestamp())}" for a, b, t in zip(scored["norad_a"], scored["norad_b"], scored["tca"], strict=True)],
            "norad_a": scored["norad_a"].astype("int64"),
            "norad_b": scored["norad_b"].astype("int64"),
            "name_a": scored["norad_a"].map(by_id["name"]),
            "name_b": scored["norad_b"].map(by_id["name"]),
            "type_a": scored["norad_a"].map(by_id["object_type"]),
            "type_b": scored["norad_b"].map(by_id["object_type"]),
            "tca": scored["tca"],
            "hours_to_tca": hours,
            "miss_distance_km": scored["miss_km"],
            "relative_speed_kms": scored["rel_speed_km_s"],
            "epoch_age_a_days": scored["norad_a"].map(by_id["epoch_age_h"]) / 24,
            "epoch_age_b_days": scored["norad_b"].map(by_id["epoch_age_h"]) / 24,
            "in_model_scope": scored["in_model_scope"].astype(bool),
            "risk_probability_estimate": scored["risk_probability_estimate"],
            "top_reasons": scored.get("top_reasons"),
        }
    ).sort_values(["tca", "norad_a", "norad_b"]).reset_index(drop=True)

    run_id = f"{snapshot_id}{run_suffix}"
    results_dir.mkdir(parents=True, exist_ok=True)
    tmp = results_dir / f".{run_id}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    store.write_parquet(objects, tmp / "objects.parquet")
    store.write_parquet(conj, tmp / "conjunctions.parquet")
    store.write_parquet(shells.assign(run_id=run_id), tmp / "shells.parquet")
    store.write_parquet(_shell_history(settings), tmp / "shells_history.parquet")
    (tmp / "model.json").write_text(json.dumps(_model_summary(model), indent=2, default=str))
    orbit_file = _orbit_file(catalog, objects, tmp)
    scored_n = int(conj["risk_probability_estimate"].notna().sum())
    meta = {
        "run_id": run_id,
        "snapshot_id": snapshot_id,
        "snapshot_time": start.isoformat(),
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "commit": git_commit(),
        "object_count": len(objects),
        "conjunction_count": len(conj),
        "scored_conjunctions": scored_n,
        "conjunction_counts": counts,
        "model_version": model.version if model else None,
        "operating_probability": model.operating_threshold if model else None,
        "history_days_for_features": None if np.isnan(coverage) else round(coverage, 1),
        "history_complete": bool(not np.isnan(coverage) and coverage >= FULL_HISTORY_DAYS),
        "orbit_file": orbit_file,
        "disclaimer": DISCLAIMER,
        "sources": "Data from CelesTrak and Space-Track.org (USSPACECOM).",
    }
    (tmp / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
    bundle = publish(tmp, results_dir, run_id, min_objects if min_objects is not None else settings.gp_min_objects)
    log.info("results published", extra={k: meta[k] for k in ("run_id", "object_count", "conjunction_count", "scored_conjunctions")})
    return bundle
