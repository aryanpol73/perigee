"""Write web/src/__fixtures__/sgp4_reference.json for the browser cross-implementation test.

Takes 25 records from the committed fixture bundle's orbit file and their TEME
positions from Python SGP4 at epoch, +6 h and +1 day. The web test propagates the
same records with satellite.js and must agree within a tolerance.

Usage: python scripts/make_web_fixture.py
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.orbits.frames import julian_dates  # noqa: E402
from src.orbits.propagate import satrec_from_elements  # noqa: E402
from src.results.bundle import POINTER  # noqa: E402
from src.results.fixture import FIXTURE_DIR  # noqa: E402

OUT = ROOT / "web" / "src" / "__fixtures__" / "sgp4_reference.json"
OFFSETS_H = (0.0, 6.0, 24.0)


def main() -> int:
    bundle = FIXTURE_DIR / json.loads((FIXTURE_DIR / POINTER).read_text())["path"]
    meta = json.loads((bundle / "meta.json").read_text())
    orbit = json.loads(gzip.decompress((bundle / meta["orbit_file"]).read_bytes()))
    fields = orbit["fields"]
    rows = [dict(zip(fields, r, strict=True)) for r in orbit["rows"]]
    leo = [r for r in rows if r["MEAN_MOTION"] > 11][:20]
    high = [r for r in rows if r["MEAN_MOTION"] < 3][:5]
    cases = []
    for r in leo + high:
        epoch = pd.Timestamp(r["EPOCH"], tz="UTC")
        sat = satrec_from_elements(
            int(r["NORAD_CAT_ID"]), epoch, r["MEAN_MOTION"], r["ECCENTRICITY"], r["INCLINATION"], r["RA_OF_ASC_NODE"],
            r["ARG_OF_PERICENTER"], r["MEAN_ANOMALY"], r["BSTAR"], r["MEAN_MOTION_DOT"], r["MEAN_MOTION_DDOT"],
        )
        points = []
        for h in OFFSETS_H:
            t = epoch + pd.Timedelta(hours=h)
            jd, fr = julian_dates([t])
            err, pos, _ = sat.sgp4(float(jd[0]), float(fr[0]))
            if err == 0:
                points.append({"time": t.isoformat(), "teme_km": [round(v, 6) for v in pos]})
        cases.append({"record": r, "points": points})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"source": "Python sgp4 2.27 via src.orbits.propagate", "cases": cases}, indent=1) + "\n")
    print(f"Wrote {len(cases)} cases to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
