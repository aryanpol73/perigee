"""Export a results bundle as static files for the site (no API server needed).

The site on Cloudflare Pages reads these files directly and answers every API
query in the browser (web/src/static-api.ts), so it shows exactly what the API
would. Every value here comes from the same builders the API uses
(src/api/views.py); the browser only filters, sorts and pages them, using the
API's own presorted orders, and assembles details from the parts below.

  index.json                 format, run id, source block, staleness limit, shard count
  meta.json                  /v1/meta
  conjunctions.json          every /v1/conjunctions item, compactly: per-row numbers, per-object names
  conjunction-extra/NN.json  per approach: the model's top reasons and the confidence note
  objects.json               every object summary plus its detail-only fields, as columns
  shells.json, shells-history.json, model.json
  orbits/<name>.json         the orbit file for the globe (uncompressed; the CDN compresses)
  sim-catalog.json           the catalog in the simulator's shape (Pyodide, src/sim/service.py)

Usage: python -m src.main export-static --out <dir>
Reading them back from a deployed site: src/results/site_fetch.py.
"""

from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from src.api import schemas as s
from src.api import views
from src.api.data import ResultsStore
from src.results.site_fetch import FORMAT

SHARDS = 64

# Detail-only object fields, beside the summary's.
OBJECT_EXTRA = ["object_id", "launch_date", "ops_status_code", "period_min", "eccentricity", "epoch"]


def shard_of(key: str, shards: int = SHARDS) -> int:
    """FNV-1a (32-bit) of the UTF-8 key, mod shards. web/src/static-api.ts computes the same."""
    h = 0x811C9DC5
    for byte in key.encode():
        h = ((h ^ byte) * 0x01000193) & 0xFFFFFFFF
    return h % shards


def _us(t) -> np.ndarray:
    """Timestamps (Series or scalar) as integer microseconds since the epoch, UTC."""
    if isinstance(t, pd.Timestamp):
        return np.datetime64(t.tz_convert("UTC").tz_localize(None).to_datetime64(), "us").astype("int64")
    return t.dt.tz_convert("UTC").dt.as_unit("us").astype("int64").to_numpy()


def conjunction_columns(st: ResultsStore) -> dict:
    """The conjunction list in the compact form the browser expands (web/src/static-api.ts).

    Derived there, and checked here so a change upstream cannot make the two differ:
    the id (snapshot_norada_noradb_tcaseconds), hours to TCA (from the TCA and the
    snapshot time, bit for bit), the ISO TCA, and the estimate fields (from the
    probability and the operating point). Names, types and data ages are per object.
    """
    c = st.conjunctions
    tca_us = _us(c["tca"])
    snap_us = int(_us(st.snapshot_time))
    snap = st.meta["snapshot_id"]
    ids = [f"{snap}_{a}_{b}_{t // 1_000_000}" for a, b, t in zip(c["norad_a"], c["norad_b"], tca_us, strict=True)]
    if ids != c["id"].tolist():
        raise ValueError("conjunction ids no longer follow snapshot_a_b_tcaseconds; update the static format")
    if not np.array_equal((tca_us - snap_us) / 3.6e9, c["hours_to_tca"].to_numpy(dtype=float)):
        raise ValueError("hours_to_tca is no longer (tca - snapshot) in hours; update the static format")
    objects: dict[str, list] = {}
    for side in "ab":
        for norad, name, kind, age in zip(c[f"norad_{side}"], c[f"name_{side}"], c[f"type_{side}"], c[f"epoch_age_{side}_days"], strict=True):
            entry = [name, kind, float(age)]
            if objects.setdefault(str(int(norad)), entry) != entry:
                raise ValueError(f"object {norad} has different names, types or ages across approaches")
    prob = [None if p is None else p for p in (views.num(x) for x in c["risk_probability_estimate"])]
    return {
        "rows": len(c),
        "snapshot_us": snap_us,
        "objects": objects,
        "a": [int(x) for x in c["norad_a"]],
        "b": [int(x) for x in c["norad_b"]],
        "tca_us": tca_us.tolist(),
        "miss_distance_km": [float(x) for x in c["miss_distance_km"]],
        "relative_speed_kms": [float(x) for x in c["relative_speed_kms"]],
        "risk_probability_estimate": prob,
        "in_model_scope": [int(bool(x)) for x in c["in_model_scope"]],
    }


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":"), allow_nan=False), encoding="utf-8")


def export(st: ResultsStore, out: Path, max_data_age_h: float = 48.0) -> dict:
    """Write the static files for one bundle into `out` (replaced). Returns index.json's content."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    source = views.source(st).model_dump(mode="json")
    meta = views.meta(st).model_dump(mode="json")
    _write(out / "meta.json", meta)

    c = st.conjunctions
    _write(out / "conjunctions.json", conjunction_columns(st))

    extra: list[dict] = [{} for _ in range(SHARDS)]
    for cid, row in c.iterrows():
        d = views.conjunction_detail(row, st)
        extra[shard_of(cid)][cid] = {
            "top_reasons": [r.model_dump(mode="json") for r in d.top_reasons],
            "confidence_note": d.confidence_note,
        }
    for shard, part in enumerate(extra):
        _write(out / "conjunction-extra" / f"{shard:02d}.json", part)

    o = st.objects
    summaries = [views.object_summary(r).model_dump(mode="json") for _, r in o.iterrows()]
    details = [views.object_detail(int(n), st, involved=c.iloc[:0]).model_dump(mode="json") for n in o.index]
    columns = {k: [s[k] for s in summaries] for k in summaries[0]} if summaries else {}
    for k in OBJECT_EXTRA:
        columns[k] = [d[k] for d in details]
    _write(out / "objects.json", {"rows": len(summaries), "columns": columns})

    _write(out / "shells.json", views.shells(st).model_dump(mode="json"))
    _write(out / "shells-history.json", views.shells_history(st).model_dump(mode="json"))
    model = views.model_summary(st)
    if model is not None:
        _write(out / "model.json", model.model_dump(mode="json"))

    orbit_name = st.meta["orbit_file"].removesuffix(".gz")
    (out / "orbits").mkdir()
    (out / "orbits" / orbit_name).write_bytes(gzip.decompress(st.orbit_file().read_bytes()))

    so = st.shell_objects()
    _write(out / "sim-catalog.json", {
        "snapshot_time": st.snapshot_time.isoformat(),
        "columns": {k: [None if isinstance(v, float) and np.isnan(v) else (v.item() if hasattr(v, "item") else v) for v in so[k]]
                    for k in so.columns},
    })

    index = {
        "format": FORMAT,
        "run_id": st.meta["run_id"],
        "source": source,
        "disclaimer": s.DISCLAIMER,
        "max_data_age_hours": max_data_age_h if np.isfinite(max_data_age_h) else None,
        "operating_probability": st.meta.get("operating_probability"),
        "history_complete": bool(st.meta.get("history_complete", False)),
        "conjunction_shards": SHARDS,
        "orbit_file": orbit_name,
        "has_model": model is not None,
        # The simulator runs in the browser (src/sim/service.py in Pyodide): the request
        # defaults and bounds, and the label, exactly as POST /v1/simulate has them.
        "simulate_defaults": s.SimulateRequest.model_validate({}).model_dump(mode="json"),
        "simulate_schema": s.SimulateRequest.model_json_schema(),
        "sim_label": s.SIM_LABEL,
        "files": sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()),
    }
    _write(out / "index.json", index)
    return index
