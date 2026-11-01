import numpy as np
import pandas as pd
import pytest

from src.ingest.parse import parse_gp
from src.orbits import satjs_bridge
from src.orbits.propagate import propagate_catalog
from src.orbits.reference import OemError, parse_oem
from tests.conftest import FIXTURES


def test_parse_nasa_oem_sample():
    oem = parse_oem((FIXTURES / "iss_oem_sample.txt").read_text())
    assert len(oem) == 5
    assert oem.index[0] == pd.Timestamp("2026-10-30T12:00:00", tz="UTC")
    assert (oem.index[1:] - oem.index[:-1] == pd.Timedelta(minutes=4)).all()
    radius = np.linalg.norm(oem[["x_km", "y_km", "z_km"]].to_numpy(), axis=1)
    assert ((radius > 6_700) & (radius < 6_850)).all()  # ISS orbital radius
    speed = np.linalg.norm(oem[["vx_km_s", "vy_km_s", "vz_km_s"]].to_numpy(), axis=1)
    assert ((speed > 7.5) & (speed < 7.8)).all()


def test_parse_oem_rejects_other_frames():
    text = (FIXTURES / "iss_oem_sample.txt").read_text().replace("EME2000", "ITRF")
    with pytest.raises(OemError, match="REF_FRAME"):
        parse_oem(text)


def test_parse_oem_rejects_empty():
    with pytest.raises(OemError):
        parse_oem("CCSDS_OEM_VERS = 2.0\n")


@pytest.mark.skipif(not satjs_bridge.available(), reason="node or scripts/satjs/node_modules missing")
def test_python_and_satellite_js_agree(gp_bytes):
    catalog = parse_gp({"stations": gp_bytes})
    times = pd.DatetimeIndex(
        ["2026-10-30T18:00:00", "2026-10-31T00:00:00", "2026-11-02T18:00:00"], tz="UTC"
    )
    ours = propagate_catalog(catalog, times)
    js_eci, js_geo = satjs_bridge.propagate(catalog, times)

    assert not np.isnan(js_eci).any()
    # satellite.js keeps epochs to the millisecond (<= ~7.5 m at LEO speed).
    assert np.linalg.norm(ours.position_teme_km - js_eci, axis=-1).max() < 0.010
    lat, _, height = ours.geodetic()
    assert np.abs(lat - js_geo[..., 0]).max() < 1e-3
    assert np.abs(height - js_geo[..., 2]).max() < 0.010


def test_omm_records_send_null_for_missing_designators(gp_bytes):
    catalog = parse_gp({"stations": gp_bytes}).head(1).copy()
    catalog.loc[0, "object_id"] = None
    record = satjs_bridge.omm_records(catalog)[0]
    assert record["OBJECT_ID"] is None
    assert record["EPOCH"] == "2026-10-29T13:31:42.192192"
