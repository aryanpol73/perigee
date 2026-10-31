"""Import Space-Track's yearly TLE archives into the element-set history.

Space-Track's API rules allow gp_history "1 / lifetime" and say that for many
objects or long date ranges you must download the yearly zip bundles from their
cloud storage instead, store them, and never download them again. So Perigee
never queries gp_history: a human downloads each yearly zip once (logged in to
space-track.org) into data/raw/spacetrack/, and this module streams it into
data/processed/elset_history/ as Parquet. No Space-Track credentials are used.

TLEs carry no publication time. `available_at` is estimated as epoch +
`publication_lag` (default 12 h, conservative): using a record a little later
than it was really available only wastes data, using it earlier would leak.

Cite the source in anything published: "Data from Space-Track.org (USSPACECOM)".
"""

from __future__ import annotations

import io
import logging
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path

import numpy as np
import pandas as pd

from src.storage import parquet_store as store
from src.storage.elsets import ELSET_HISTORY

log = logging.getLogger("perigee.ingest.spacetrack_import")

DEFAULT_LAG = pd.Timedelta(hours=12)
ALPHA5 = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # I and O are not used


class TleError(ValueError):
    pass


def alpha5_to_int(field: str) -> int:
    """'25544' -> 25544; Alpha-5 'A0000' -> 100000 ('I' and 'O' are skipped)."""
    field = field.strip()
    if not field:
        raise TleError("empty catalog number")
    if field[0].isdigit():
        return int(field)
    if field[0] not in ALPHA5:
        raise TleError(f"bad Alpha-5 catalog number {field!r}")
    return (ALPHA5.index(field[0]) + 10) * 10_000 + int(field[1:])


def _implied(field: str) -> float:
    """TLE implied-decimal exponent field, e.g. ' 28098-4' -> 0.28098e-4."""
    s = field.strip()
    if not s or s in ("00000-0", "00000+0", "0"):
        return 0.0
    sign = -1.0 if s[0] == "-" else 1.0
    s = s.lstrip("+-")
    mantissa, exp = s[:-2], s[-2:]
    return sign * float(f"0.{mantissa}") * 10 ** int(exp)


def _checksum_ok(line: str) -> bool:
    if len(line) < 69 or not line[68].isdigit():
        return True  # no checksum to verify
    total = sum(int(c) if c.isdigit() else (1 if c == "-" else 0) for c in line[:68])
    return total % 10 == int(line[68])


def parse_tle(line1: str, line2: str, name: str | None = None) -> dict:
    """One TLE -> OMM-style fields matching CelesTrak's CSV units."""
    if not (line1.startswith("1 ") and line2.startswith("2 ")):
        raise TleError("not a TLE pair")
    if not (_checksum_ok(line1) and _checksum_ok(line2)):
        raise TleError("checksum mismatch")
    year = int(line1[18:20])
    year += 2000 if year < 57 else 1900
    day = float(line1[20:32])
    epoch = pd.Timestamp(year=year, month=1, day=1, tz="UTC") + pd.to_timedelta(day - 1.0, unit="D")
    intl = line1[9:17].strip()
    object_id = None
    if len(intl) >= 5:
        yy = int(intl[:2])
        object_id = f"{(2000 if yy < 57 else 1900) + yy}-{intl[2:5]}{intl[5:]}"
    return {
        "norad_id": alpha5_to_int(line1[2:7]),
        "object_name": name,
        "object_id": object_id,
        "epoch": epoch,
        "mean_motion": float(line2[52:63]),
        "eccentricity": float("0." + line2[26:33].strip()),
        "inclination": float(line2[8:16]),
        "ra_of_asc_node": float(line2[17:25]),
        "arg_of_pericenter": float(line2[34:42]),
        "mean_anomaly": float(line2[43:51]),
        "bstar": _implied(line1[53:61]),
        "mean_motion_dot": float(line1[33:43]),
        "mean_motion_ddot": _implied(line1[44:52]),
    }


def iter_tles(lines: Iterable[str]) -> Iterator[tuple[str, str, str | None]]:
    """Yield (line1, line2, name) from 2LE or 3LE text, skipping anything malformed."""
    name: str | None = None
    pending: str | None = None
    for raw in lines:
        line = raw.rstrip("\r\n")
        if line.startswith("1 ") and len(line) >= 64:
            pending = line
        elif line.startswith("2 ") and pending is not None:
            yield pending, line, name
            pending, name = None, None
        elif line.strip():
            name = line[2:].strip() if line.startswith("0 ") else line.strip()
            pending = None


def _text_lines(path: Path) -> Iterator[str]:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            for member in z.namelist():
                if member.endswith("/"):
                    continue
                with z.open(member) as f:
                    yield from io.TextIOWrapper(f, encoding="ascii", errors="replace")
    else:
        with path.open(encoding="ascii", errors="replace") as f:
            yield from f


def import_archive(
    path: Path,
    processed_dir: Path,
    since: pd.Timestamp | None = None,
    until: pd.Timestamp | None = None,
    lag: pd.Timedelta = DEFAULT_LAG,
    batch_rows: int = 1_000_000,
) -> dict:
    """Stream a TLE archive (zip or text) into Parquet parts. Idempotent per archive name."""
    out_dir = processed_dir / ELSET_HISTORY
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = path.name.split(".")[0]
    for old in out_dir.glob(f"{stem}_part*.parquet"):
        old.unlink()  # re-import replaces this archive's parts, never others

    counts = {"archive": path.name, "parsed": 0, "skipped_malformed": 0, "outside_window": 0, "parts": 0}
    batch: list[dict] = []

    def flush() -> None:
        if not batch:
            return
        df = pd.DataFrame(batch)
        df["available_at"] = df["epoch"] + lag
        store.write_parquet(df, out_dir / f"{stem}_part{counts['parts']:04d}.parquet")
        counts["parts"] += 1
        batch.clear()

    for line1, line2, name in iter_tles(_text_lines(path)):
        try:
            rec = parse_tle(line1, line2, name)
        except (TleError, ValueError):
            counts["skipped_malformed"] += 1
            continue
        if (since is not None and rec["epoch"] < since) or (until is not None and rec["epoch"] >= until):
            counts["outside_window"] += 1
            continue
        batch.append(rec)
        counts["parsed"] += 1
        if len(batch) >= batch_rows:
            flush()
    flush()
    log.info("imported Space-Track archive", extra=counts)
    return counts


def sanity_check(processed_dir: Path) -> dict:
    """Quick summary of what has been imported."""
    glob = store.sql_path(processed_dir / ELSET_HISTORY) + "/*.parquet"
    con = store.connect()
    try:
        row = con.execute(
            f"SELECT count(*) AS n, count(DISTINCT norad_id) AS objects, min(epoch) AS first, max(epoch) AS last "
            f"FROM read_parquet('{glob}')"
        ).df().iloc[0]
    finally:
        con.close()
    return {k: (int(v) if isinstance(v, (int, np.integer)) else str(v)) for k, v in row.items()}
