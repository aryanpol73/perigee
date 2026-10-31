import zipfile

import numpy as np
import pandas as pd
import pytest
from sgp4.api import WGS72, Satrec

from src.ingest.spacetrack_import import TleError, alpha5_to_int, import_archive, iter_tles, parse_tle, sanity_check
from src.orbits.propagate import satrec_from_elements
from src.storage.elsets import ElsetHistory

def _checksummed(line68: str) -> str:
    total = sum(int(c) if c.isdigit() else (1 if c == "-" else 0) for c in line68)
    return line68 + str(total % 10)


# Vallado's test TLE (real check digits) plus an ISS TLE in 3LE form, built here
# with correct check digits.
VALLADO = (
    "1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753",
    "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667",
)
ISS = (
    "0 ISS (ZARYA)",
    _checksummed("1 25544U 98067A   26302.56368277  .00010765  00000-0  19312-3 0  999"),
    _checksummed("2 25544  51.6333 355.1294 0007050 324.3141  35.7375 15.4910084259258"),
)


@pytest.mark.parametrize(("line1", "line2"), [VALLADO, ISS[1:]])
def test_parsed_elements_propagate_exactly_like_sgp4s_own_parser(line1, line2):
    rec = parse_tle(line1, line2)
    ours = satrec_from_elements(
        rec["norad_id"], rec["epoch"], rec["mean_motion"], rec["eccentricity"], rec["inclination"],
        rec["ra_of_asc_node"], rec["arg_of_pericenter"], rec["mean_anomaly"], rec["bstar"],
        rec["mean_motion_dot"], rec["mean_motion_ddot"],
    )
    ref = Satrec.twoline2rv(line1, line2, WGS72)
    for minutes in (0.0, 720.0, 4320.0):
        np.testing.assert_allclose(ours.sgp4_tsince(minutes)[1], ref.sgp4_tsince(minutes)[1], atol=1e-6)


def test_parse_fields():
    rec = parse_tle(*VALLADO)
    assert rec["norad_id"] == 5
    assert rec["object_id"] == "1958-002B"
    assert rec["epoch"] == pd.Timestamp("2000-06-27T18:50:19.733568", tz="UTC")
    assert rec["bstar"] == pytest.approx(0.28098e-4)
    assert rec["eccentricity"] == pytest.approx(0.1859667)


def test_alpha5_catalog_numbers():
    assert alpha5_to_int("25544") == 25544
    assert alpha5_to_int("A0000") == 100000
    assert alpha5_to_int("H5544") == 175544  # H is the 8th Alpha-5 letter
    assert alpha5_to_int("J0001") == 180001  # I is skipped
    with pytest.raises(TleError):
        alpha5_to_int("I0000")


def test_checksum_rejects_corrupt_lines():
    bad = VALLADO[0][:20] + "8" + VALLADO[0][21:]
    with pytest.raises(TleError, match="checksum"):
        parse_tle(bad, VALLADO[1])


def test_iter_tles_handles_2le_3le_and_junk():
    text = ["junk line", *ISS, *VALLADO, "1 truncated", ""]
    found = list(iter_tles(text))
    assert [(n, l1[2:7]) for l1, _, n in found] == [("ISS (ZARYA)", "25544"), (None, "00005")]


def test_import_zip_into_history(tmp_path):
    archive = tmp_path / "tle2026.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("tle2026.txt", "\n".join([*ISS, *VALLADO]) + "\n")
    counts = import_archive(archive, tmp_path / "processed", since=pd.Timestamp("2026-01-01", tz="UTC"))
    assert counts["parsed"] == 1 and counts["outside_window"] == 1 and counts["parts"] == 1

    h = ElsetHistory(tmp_path / "processed")
    iss = h.catalog_at(pd.Timestamp("2026-10-30T12:00", tz="UTC"))
    assert iss["norad_id"].tolist() == [25544]
    # Not available until epoch + 12 h (conservative publication lag).
    epoch = iss["epoch"].iloc[0]
    assert h.catalog_at(epoch + pd.Timedelta(hours=11)).empty
    assert sanity_check(tmp_path / "processed")["objects"] == 1

    # Re-importing the same archive replaces its parts instead of duplicating them.
    import_archive(archive, tmp_path / "processed", since=pd.Timestamp("2026-01-01", tz="UTC"))
    assert sanity_check(tmp_path / "processed")["n"] == 1
