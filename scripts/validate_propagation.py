"""Phase 2 validation: Perigee's SGP4 + frames against independent references.

1. ISS vs NASA's flight-dynamics ephemeris (JSC FOD/TOPO OEM, EME2000), with
   astropy doing the trusted EME2000/TEME -> Earth-fixed conversions.
2. Perigee's TEME -> Earth-fixed rotation vs astropy's (frames code alone).
3. Python sgp4 vs satellite.js on a sample of the catalog.

Usage: python scripts/validate_propagation.py [--oem PATH] [--sample N]
Needs requirements-dev.txt (astropy) and `npm install` in scripts/satjs.
Writes docs/validation.md.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from astropy import units as u
from astropy.coordinates import GCRS, ITRS, TEME, CartesianRepresentation
from astropy.time import Time
from astropy.utils import iers

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import Settings  # noqa: E402
from src.orbits import satjs_bridge  # noqa: E402
from src.orbits.frames import geodetic_to_ecef  # noqa: E402
from src.orbits.propagate import propagate_catalog  # noqa: E402
from src.orbits.reference import ISS_OEM_URL, parse_oem  # noqa: E402
from src.storage import parquet_store as store  # noqa: E402

ISS = 25544
DEFAULT_OEM = ROOT / "data" / "reference" / "ISS.OEM_J2K_EPH.txt"

# Use the IERS tables bundled with astropy-iers-data; never download at run time.
iers.conf.auto_download = False


def latest_catalog(settings: Settings) -> pd.DataFrame:
    con = store.connect()
    try:
        catalog = store.catalog_relation(settings.processed_dir)
        return con.execute(
            f"SELECT * FROM {catalog} WHERE snapshot_id = (SELECT max(snapshot_id) FROM {catalog}) "
            "AND sgp4_error = 0 ORDER BY norad_id"
        ).df()
    finally:
        con.close()


def _cartesian(xyz_km: np.ndarray) -> CartesianRepresentation:
    return CartesianRepresentation(xyz_km.T * u.km)


def _xyz(frame) -> np.ndarray:
    return np.asarray(frame.cartesian.xyz.to_value(u.km)).T


def ric_components(delta: np.ndarray, r: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Split position differences into radial, in-track and cross-track parts."""
    radial = r / np.linalg.norm(r, axis=1, keepdims=True)
    cross = np.cross(r, v)
    cross /= np.linalg.norm(cross, axis=1, keepdims=True)
    in_track = np.cross(cross, radial)
    return np.stack(
        [np.sum(delta * radial, 1), np.sum(delta * in_track, 1), np.sum(delta * cross, 1)], 1
    )


def iss_vs_nasa(catalog: pd.DataFrame, oem: pd.DataFrame) -> dict:
    row = catalog[catalog["norad_id"] == ISS]
    if row.empty:
        raise SystemExit("ISS (25544) not in the latest catalog snapshot")
    epoch = pd.Timestamp(row["epoch"].iloc[0])

    window = oem[(oem.index >= epoch - pd.Timedelta(hours=12)) & (oem.index <= epoch + pd.Timedelta(hours=24))]
    if window.empty:
        raise SystemExit(f"NASA OEM does not cover the ISS epoch {epoch}; download a fresh one")
    times = window.index
    obstime = Time(times.to_pydatetime(), scale="utc")

    prop = propagate_catalog(row, times)
    teme = prop.position_teme_km[0]
    ours_ecef = prop.ecef_km()[0]

    ref_r = window[["x_km", "y_km", "z_km"]].to_numpy()
    ref_v = window[["vx_km_s", "vy_km_s", "vz_km_s"]].to_numpy()
    # EME2000 and GCRS differ by a frame bias of ~20 mas (under a metre at ISS radius).
    ref_gcrs = GCRS(_cartesian(ref_r), obstime=obstime, representation_type="cartesian")
    ref_itrs = _xyz(ref_gcrs.transform_to(ITRS(obstime=obstime)))

    ours_teme = TEME(_cartesian(teme), obstime=obstime, representation_type="cartesian")
    ours_gcrs = _xyz(ours_teme.transform_to(GCRS(obstime=obstime)))
    astropy_itrs = _xyz(ours_teme.transform_to(ITRS(obstime=obstime)))

    hours = (times - epoch) / pd.Timedelta(hours=1)
    sgp4_error = np.linalg.norm(ours_gcrs - ref_r, axis=1)
    ric = ric_components(ours_gcrs - ref_r, ref_r, ref_v)
    end_to_end = np.linalg.norm(ours_ecef - ref_itrs, axis=1)
    frames_only = np.linalg.norm(ours_ecef - astropy_itrs, axis=1)

    at_epoch = int(np.argmin(np.abs(hours)))
    return {
        "epoch": epoch,
        "points": len(times),
        "nearest_hours": float(hours[at_epoch]),
        "error_at_epoch_km": float(sgp4_error[at_epoch]),
        "ric_at_epoch_km": ric[at_epoch],
        "end_to_end_at_epoch_km": float(end_to_end[at_epoch]),
        "bins": [
            (label, float(sgp4_error[mask].max()), float(np.median(sgp4_error[mask])))
            for label, mask in [
                ("within 1 h of epoch", np.abs(hours) <= 1),
                ("12 h before to epoch", (hours >= -12) & (hours <= 0)),
                ("epoch to +12 h", (hours >= 0) & (hours <= 12)),
                ("+12 h to +24 h", (hours > 12) & (hours <= 24)),
            ]
            if mask.any()
        ],
        "frames_max_m": float(frames_only.max() * 1000),
        "frames_median_m": float(np.median(frames_only) * 1000),
        "radius_diff_max_km": float(
            np.abs(np.linalg.norm(teme, axis=1) - np.linalg.norm(ref_r, axis=1)).max()
        ),
    }


def python_vs_satjs(catalog: pd.DataFrame, sample: int, snapshot_time: pd.Timestamp) -> dict:
    rng = np.random.default_rng(42)
    picks = rng.choice(len(catalog), size=min(sample, len(catalog)), replace=False)
    subset = catalog.iloc[np.sort(picks)].reset_index(drop=True)
    times = pd.DatetimeIndex(
        [snapshot_time + pd.Timedelta(hours=h) for h in (0, 6, 24, 72)]
    ).tz_convert("UTC")

    prop = propagate_catalog(subset, times)
    lat, lon, height = prop.geodetic()
    js_eci, js_geo = satjs_bridge.propagate(subset, times)

    both = prop.ok & ~np.isnan(js_eci[..., 0])
    position_m = np.linalg.norm(prop.position_teme_km - js_eci, axis=-1)[both] * 1000
    # Ground distance between the two sub-satellite points; raw longitude
    # differences are meaningless near the poles.
    ground_m = (
        np.linalg.norm(
            geodetic_to_ecef(lat, lon, 0.0) - geodetic_to_ecef(js_geo[..., 0], js_geo[..., 1], 0.0),
            axis=-1,
        )[both]
        * 1000
    )
    dheight_m = np.abs(height - js_geo[..., 2])[both] * 1000

    # satellite.js parses EPOCH through a JS Date (millisecond precision).
    # Repeat with epochs truncated the same way to isolate the SGP4 maths.
    truncated = subset.copy()
    truncated["epoch"] = truncated["epoch"].dt.floor("ms")
    position_ms_m = (
        np.linalg.norm(propagate_catalog(truncated, times).position_teme_km - js_eci, axis=-1)[both]
        * 1000
    )
    deep = (2 * np.pi / (subset["mean_motion"].to_numpy() * 2 * np.pi / 1440.0)) >= 225.0
    return {
        "objects": len(subset),
        "deep_space": int(deep.sum()),
        "times": [f"+{h} h" for h in (0, 6, 24, 72)],
        "compared": int(both.sum()),
        "flag_mismatches": int((prop.ok != ~np.isnan(js_eci[..., 0])).sum()),
        "position_max_m": float(position_m.max()),
        "position_p99_m": float(np.percentile(position_m, 99)),
        "position_median_m": float(np.median(position_m)),
        "position_ms_max_m": float(position_ms_m.max()),
        "position_ms_median_m": float(np.median(position_ms_m)),
        "ground_max_m": float(ground_m.max()),
        "ground_median_m": float(np.median(ground_m)),
        "height_max_m": float(dheight_m.max()),
        "height_median_m": float(np.median(dheight_m)),
    }


def write_report(path: Path, iss: dict, parity: dict, snapshot_id: str) -> str:
    r, i, c = iss["ric_at_epoch_km"]
    lines = [
        "# Propagation validation (Phase 2)",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `scripts/validate_propagation.py` "
        f"from catalog snapshot `{snapshot_id}`.",
        "",
        "## 1. ISS against NASA's flight-dynamics ephemeris",
        "",
        f"Reference: [NASA JSC FOD/TOPO ISS OEM]({ISS_OEM_URL}), EME2000 state vectors that do not "
        "come from the public TLE/GP data. Frame conversions on the reference side are done by astropy.",
        "",
        f"ISS element set epoch: {iss['epoch']:%Y-%m-%d %H:%M:%S} UTC, compared at {iss['points']} "
        "reference points (4-minute spacing) from 12 h before to 24 h after epoch.",
        "",
        "| | Value |",
        "|---|---|",
        f"| Position error nearest epoch ({iss['nearest_hours']:+.2f} h) | **{iss['error_at_epoch_km']:.2f} km** |",
        f"| of which radial / in-track / cross-track | {r:+.2f} / {i:+.2f} / {c:+.2f} km |",
        f"| End to end in Earth-fixed frame (SGP4 + Perigee frames) | {iss['end_to_end_at_epoch_km']:.2f} km |",
        f"| Largest orbital-radius difference over the window | {iss['radius_diff_max_km']:.2f} km |",
        "",
        "| Window | Max error | Median error |",
        "|---|---|---|",
        *(f"| {label} | {mx:.2f} km | {md:.2f} km |" for label, mx, md in iss["bins"]),
        "",
        "Public element sets are fits to tracking data with roughly kilometre-level accuracy at "
        "epoch, and the error is mostly in-track and grows with time from epoch. This is the "
        "uncertainty every later stage (screening, ML) has to live with. The NASA file is itself a "
        "prediction, made when it was published, so part of the difference far from epoch belongs to it.",
        "",
        "## 2. Frames code alone",
        "",
        "Perigee's TEME to Earth-fixed rotation (IAU-82 GMST, UTC for UT1, no polar motion) against "
        "astropy's TEME to ITRS on the same SGP4 positions:",
        f"median **{iss['frames_median_m']:.0f} m**, max {iss['frames_max_m']:.0f} m. "
        "The simplifications cost tens of metres, two orders of magnitude below the TLE error.",
        "",
        "## 3. Python sgp4 against satellite.js (the browser library)",
        "",
        f"{parity['objects']} randomly sampled catalog objects ({parity['deep_space']} deep-space, "
        f"which use the SDP4 branch), propagated to snapshot time {', '.join(parity['times'])}: "
        f"{parity['compared']} position pairs.",
        "",
        "| | Max | 99th percentile | Median |",
        "|---|---|---|---|",
        f"| TEME position | {parity['position_max_m']:.2f} m | {parity['position_p99_m']:.2f} m "
        f"| {parity['position_median_m']:.2f} m |",
        f"| TEME position, epochs truncated to ms | {parity['position_ms_max_m']:.3f} m | | "
        f"{parity['position_ms_median_m']:.3f} m |",
        f"| Ground point (lat/lon) | {parity['ground_max_m']:.1f} m | | {parity['ground_median_m']:.1f} m |",
        f"| Height | {parity['height_max_m']:.1f} m | | {parity['height_median_m']:.1f} m |",
        "",
        f"Objects flagged as failed by one library but not the other: {parity['flag_mismatches']}.",
        "",
        "The few-metre position difference comes from satellite.js reading `EPOCH` through a "
        "JavaScript `Date`, which keeps only milliseconds (1 ms is about 7.5 m of LEO motion). With "
        "Python's epochs truncated the same way the two agree to centimetres, so the SGP4 maths is "
        "identical. Small ground and height differences also come from the geodetic conversion "
        "(satellite.js uses its own WGS-84 constants and iteration).",
        "",
    ]
    text = "\n".join(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oem", type=Path, default=DEFAULT_OEM)
    parser.add_argument("--sample", type=int, default=500)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "validation.md")
    args = parser.parse_args()

    if not args.oem.exists():
        raise SystemExit(f"Missing {args.oem}. Download it once from {ISS_OEM_URL}")
    if not satjs_bridge.available():
        raise SystemExit("satellite.js not installed: run `npm install` in scripts/satjs")

    settings = Settings.from_env()
    catalog = latest_catalog(settings)
    snapshot_id = str(catalog["snapshot_id"].iloc[0])
    snapshot_time = pd.Timestamp(catalog["snapshot_time"].iloc[0]).tz_convert("UTC")
    oem = parse_oem(args.oem.read_text(encoding="utf-8"))

    iss = iss_vs_nasa(catalog, oem)
    parity = python_vs_satjs(catalog, args.sample, snapshot_time)
    print(write_report(args.out, iss, parity, snapshot_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
