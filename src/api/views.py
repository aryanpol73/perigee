"""Response bodies built from a results bundle, shared by the API routes and the
static site export (src/results/static_site.py), so both always say the same thing.

Each function takes the loaded bundle and returns a typed model; nothing here
knows about HTTP.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from src.api import schemas as s
from src.api.data import ResultsStore

MAX_LIMIT = 500  # longest list one response carries (pages, an object's approaches)


def num(x) -> float | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) or pd.isna(x) else float(x)


def source(st: ResultsStore) -> s.Source:
    return s.Source(
        snapshot_id=st.meta["snapshot_id"],
        generated_at=st.meta["generated_at"],
        model_version=st.meta["model_version"],
        history_complete=bool(st.meta.get("history_complete", False)),
    )


def meta(st: ResultsStore, orbit_file_url: str | None = None) -> s.Meta:
    m = st.meta
    return s.Meta(
        snapshot_id=m["snapshot_id"],
        snapshot_time=m["snapshot_time"],
        generated_at=m["generated_at"],
        commit=m["commit"],
        object_count=m["object_count"],
        conjunction_count=m["conjunction_count"],
        scored_conjunctions=m["scored_conjunctions"],
        same_constellation_left_out=m["conjunction_counts"]["same_constellation_left_out"],
        model_version=m["model_version"],
        operating_probability=m["operating_probability"],
        history_complete=m["history_complete"],
        history_days_for_features=m["history_days_for_features"],
        orbit_file_url=orbit_file_url or f"/v1/orbits/{m['orbit_file']}",
        disclaimer=m["disclaimer"],
        sources=m["sources"],
    )


def object_summary(row: pd.Series) -> s.ObjectSummary:
    return s.ObjectSummary(
        norad_id=int(row["norad_id"]),
        name=row["name"],
        object_type=row["object_type"],
        rcs_size=row["rcs_size"] if isinstance(row["rcs_size"], str) else None,
        owner=row["owner"] if isinstance(row["owner"], str) else None,
        regime=row["regime"] if isinstance(row["regime"], str) else None,
        perigee_km=float(row["perigee_km"]),
        apogee_km=float(row["apogee_km"]),
        inclination_deg=float(row["inclination_deg"]),
        epoch_age_days=float(row["epoch_age_h"]) / 24,
    )


def conjunction(row: pd.Series, st: ResultsStore) -> s.Conjunction:
    p = num(row["risk_probability_estimate"])
    op = st.meta.get("operating_probability")
    return s.Conjunction(
        id=row["id"],
        tca=row["tca"],
        hours_to_tca=float(row["hours_to_tca"]),
        miss_distance_km=float(row["miss_distance_km"]),
        relative_speed_kms=float(row["relative_speed_kms"]),
        risk_probability_estimate=p,
        in_model_scope=bool(row["in_model_scope"]),
        above_operating_point=None if p is None or op is None else p >= op,
        estimate_status="none" if p is None else "standard" if st.meta.get("history_complete", False) else "provisional",
        objects=[
            s.OrbitedObject(norad_id=int(row["norad_a"]), name=row["name_a"], object_type=row["type_a"], epoch_age_days=float(row["epoch_age_a_days"])),
            s.OrbitedObject(norad_id=int(row["norad_b"]), name=row["name_b"], object_type=row["type_b"], epoch_age_days=float(row["epoch_age_b_days"])),
        ],
    )


def conjunction_detail(row: pd.Series, st: ResultsStore) -> s.ConjunctionDetail:
    base = conjunction(row, st)
    reasons = json.loads(row["top_reasons"]) if isinstance(row["top_reasons"], str) else []
    ages = sorted([row["epoch_age_a_days"], row["epoch_age_b_days"]])
    note = (
        f"Orbit data for the two objects is {ages[0]:.1f} and {ages[1]:.1f} days old at the snapshot. Public orbit "
        f"data is accurate to roughly a kilometre and the error grows with age, so a predicted "
        f"{row['miss_distance_km']:.2f} km miss could easily be several kilometres either way."
    )
    if not base.in_model_scope:
        note += " This approach is outside the model's scope (more than 3 days ahead), so it has no estimate."
    elif not st.meta.get("history_complete", False):
        note += " " + s.PROVISIONAL_NOTE
    objs = [object_summary(st.objects.loc[int(n)]) for n in (row["norad_a"], row["norad_b"])]
    return s.ConjunctionDetail(**base.model_dump(), source=source(st), object_details=objs,
                               top_reasons=[s.Reason(**r) for r in reasons], confidence_note=note)


def object_detail(norad_id: int, st: ResultsStore, involved: pd.DataFrame | None = None) -> s.ObjectDetail:
    """`involved`: this object's approaches, if the caller already has them grouped."""
    row = st.objects.loc[norad_id]
    if involved is None:
        c = st.conjunctions
        involved = c[(c["norad_a"] == norad_id) | (c["norad_b"] == norad_id)]
    # Stable: ties keep bundle order, as the static site's presorted "tca" order does.
    mine = involved.sort_values("tca", kind="stable").head(MAX_LIMIT)
    return s.ObjectDetail(
        **object_summary(row).model_dump(),
        source=source(st),
        # Analyst objects have no SATCAT record: no international designator.
        object_id=row["object_id"] if isinstance(row["object_id"], str) else None,
        launch_date=None if pd.isna(row["launch_date"]) else row["launch_date"],
        ops_status_code=row["ops_status_code"] if isinstance(row["ops_status_code"], str) else None,
        period_min=float(row["period_min"]),
        eccentricity=float(row["eccentricity"]),
        epoch=row["epoch"],
        conjunctions=[conjunction(r, st) for _, r in mine.iterrows()],
    )


def shells(st: ResultsStore) -> s.Shells:
    bands = [
        s.ShellBand(
            alt_lo_km=r.alt_lo_km, alt_hi_km=r.alt_hi_km, objects=r.objects, density_per_km3=r.density_per_km3,
            mean_rel_speed_km_s=num(r.mean_rel_speed_km_s), risk_index=r.risk_index, risk_index_weighted=r.risk_index_weighted,
        )
        for r in st.shells.itertuples()
    ]
    return s.Shells(source=source(st), bands=bands)


def shells_history(st: ResultsStore) -> s.ShellHistory:
    runs = [
        s.ShellRun(
            run_id=str(run_id),
            source=str(g["source"].iloc[0]),
            bands=[{"alt_lo_km": float(r.alt_lo_km), "risk_index": float(r.risk_index), "risk_index_weighted": float(r.risk_index_weighted)} for r in g.itertuples()],
        )
        for run_id, g in st.shells_history.groupby("run_id", sort=True)
    ]
    return s.ShellHistory(runs=runs)


def model_summary(st: ResultsStore) -> s.ModelSummary | None:
    return s.ModelSummary(**st.model) if st.model else None
