"""Compare a Perigee screening run with CelesTrak SOCRATES for the same period.

SOCRATES (celestrak.org/SOCRATES) screens active payloads against the full
public catalog for 7 days with a 5 km threshold, using STK's SGP4. Its CSV gives,
per event, each object's days-since-epoch (DSE), so the exact element set it
used can be recovered (epoch = TCA - DSE). That separates three very different
reasons two lists can disagree:

* an object is missing from our catalog (CelesTrak's GP groups are not the
  full catalog);
* the two runs used different element sets (data age): a real effect, and the
  one the ML stage studies;
* identical inputs, different answer: a genuine screening difference.

SOCRATES is used for validation only, never as ML labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# DSE is published to 0.001 day (86.4 s); epochs within this are the same element set.
SAME_EPOCH = pd.Timedelta(seconds=90)
MATCH_TCA = pd.Timedelta(seconds=60)
COLOCATED_SPEED_KM_S = 0.01


def load_socrates(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["TCA"] = pd.to_datetime(df["TCA"], utc=True, format="ISO8601")
    df["a"] = df[["NORAD_CAT_ID_1", "NORAD_CAT_ID_2"]].min(axis=1)
    df["b"] = df[["NORAD_CAT_ID_1", "NORAD_CAT_ID_2"]].max(axis=1)
    return df


def socrates_epochs(socrates: pd.DataFrame) -> pd.Series:
    """The element-set epoch SOCRATES used for each object (median over its events)."""
    parts = [
        pd.DataFrame(
            {
                "norad_id": socrates[f"NORAD_CAT_ID_{k}"],
                "epoch": socrates["TCA"] - pd.to_timedelta(socrates[f"DSE_{k}"], unit="D"),
            }
        )
        for k in ("1", "2")
    ]
    return pd.concat(parts).groupby("norad_id")["epoch"].median()


def _same_inputs(df: pd.DataFrame, a: str, b: str, ours: pd.Series, theirs: pd.Series) -> pd.Series:
    ok = pd.Series(True, index=df.index)
    for col in (a, b):
        diff = (df[col].map(ours) - df[col].map(theirs)).abs()
        ok &= (diff < SAME_EPOCH).fillna(False).astype(bool)
    return ok


def _nearest_match(left: pd.DataFrame, lt: str, right: pd.DataFrame, rt: str, cols: list[str]) -> pd.DataFrame:
    """For each left row, the same-pair right row whose TCA is nearest (within MATCH_TCA)."""
    lhs = left.sort_values(lt).reset_index()
    r = right.sort_values(rt)[["a", "b", rt, *cols]]
    merged = pd.merge_asof(lhs, r, left_on=lt, right_on=rt, by=["a", "b"], tolerance=MATCH_TCA, direction="nearest")
    return merged.set_index("index").reindex(left.index)


@dataclass
class Comparison:
    window: tuple[pd.Timestamp, pd.Timestamp]
    socrates: pd.DataFrame  # SOCRATES events in the window, with found/reason columns
    ours: pd.DataFrame  # our events in the window, with in_socrates/reason columns


def compare(
    ours: pd.DataFrame,
    socrates: pd.DataFrame,
    catalog: pd.DataFrame,
    satcat: pd.DataFrame | None,
    edge: pd.Timedelta = pd.Timedelta(minutes=5),
) -> Comparison:
    """Match events both ways and give every disagreement a reason."""
    our_epoch = catalog.set_index("norad_id")["epoch"]
    their_epoch = socrates_epochs(socrates)
    in_catalog = set(catalog["norad_id"])
    names = catalog.set_index("norad_id")["object_name"].fillna("")
    types = satcat.set_index("norad_id")["object_type"] if satcat is not None else pd.Series(dtype=str)

    lo = max(ours["tca"].min(), socrates["TCA"].min()) + edge
    hi = min(ours["tca"].max(), socrates["TCA"].max()) - edge

    s = socrates[(socrates["TCA"] >= lo) & (socrates["TCA"] <= hi)].copy()
    s["colocated"] = s["TCA_RELATIVE_SPEED"] < COLOCATED_SPEED_KM_S
    o = ours[(ours["tca"] >= lo) & (ours["tca"] <= hi)].rename(columns={"norad_a": "a", "norad_b": "b"}).copy()

    for df in (s, o):
        sa = df["a"].map(names).fillna("").str.startswith("STARLINK")
        sb = df["b"].map(names).fillna("").str.startswith("STARLINK")
        df["kind"] = np.select([sa & sb, sa | sb], ["Starlink-Starlink", "Starlink-other"], "other")
        # Same constellation or satellite family: the first word of both names
        # matches (STARLINK-1234 / STARLINK-5678, ONEWEB-0113 / ONEWEB-0191).
        fa = df["a"].map(names).map(family)
        fb = df["b"].map(names).map(family)
        df["same_family"] = (fa == fb) & fa.notna()

    # SOCRATES -> ours (recall). Co-located pairs are compared by pair, not by TCA.
    hit = _nearest_match(s, "TCA", o[~o["colocated"]], "tca", ["miss_km"])
    s["found"] = hit["tca"].notna()
    s["our_tca"] = hit["tca"]
    s["our_miss_km"] = hit["miss_km"]
    # Objects flying together are kept once per pair (at their closest point,
    # which may fall outside the window), while SOCRATES lists one per orbit:
    # compare those by pair over both full runs.
    our_pairs = set(zip(ours["norad_a"], ours["norad_b"], strict=True))
    co = s["colocated"]
    if co.any():
        s.loc[co, "found"] = [(a, b) in our_pairs for a, b in zip(s.loc[co, "a"], s.loc[co, "b"], strict=True)]
    s["both_in_catalog"] = s["a"].isin(in_catalog) & s["b"].isin(in_catalog)
    s["same_inputs"] = s["both_in_catalog"] & _same_inputs(s, "a", "b", our_epoch, their_epoch)

    missing_type = pd.Series(
        np.where(~s["a"].isin(in_catalog), s["a"].map(types), s["b"].map(types)), index=s.index, dtype="object"
    ).fillna("?")
    s["reason"] = np.select(
        [s["found"], ~s["both_in_catalog"], ~s["same_inputs"]],
        ["found", "object not in our catalog (" + missing_type + ")", "different element sets"],
        "same inputs, not found",
    )

    # ours -> SOCRATES (precision).
    back = _nearest_match(o[~o["colocated"]], "tca", s, "TCA", ["TCA_RANGE"])
    o["in_socrates"] = False
    o.loc[back.index, "in_socrates"] = back["TCA"].notna()
    their_pairs = set(zip(socrates["a"], socrates["b"], strict=True))
    oc = o["colocated"]
    if oc.any():
        o.loc[oc, "in_socrates"] = [(a, b) in their_pairs for a, b in zip(o.loc[oc, "a"], o.loc[oc, "b"], strict=True)]
    o["socrates_has_objects"] = o["a"].isin(their_epoch.index) & o["b"].isin(their_epoch.index)
    o["same_inputs"] = o["socrates_has_objects"] & _same_inputs(o, "a", "b", our_epoch, their_epoch)
    o["reason"] = np.select(
        [
            o["in_socrates"],
            ~o["socrates_has_objects"],
            ~o["same_inputs"],
            o["same_family"],
        ],
        [
            "in SOCRATES",
            "object absent from this SOCRATES run",
            "different element sets",
            "same inputs: same-constellation pair omitted by SOCRATES",
        ],
        "same inputs, not in SOCRATES",
    )
    return Comparison(window=(lo, hi), socrates=s, ours=o)


def family(name: object) -> str | None:
    """Constellation or satellite family: the first word of the name (STARLINK, ONEWEB)."""
    if not isinstance(name, str) or not name:
        return None
    word = name.replace("-", " ").split()[0]
    return word if word.isalpha() and len(word) > 2 else None
