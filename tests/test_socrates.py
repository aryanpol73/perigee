import pandas as pd
import pytest

from src.screening.socrates import compare, socrates_epochs

T0 = pd.Timestamp("2026-11-01T00:00:00", tz="UTC")
EPOCH = pd.Timestamp("2026-10-31T00:00:00", tz="UTC")


def _catalog():
    rows = [
        (1, EPOCH, "STARLINK-1"),
        (2, EPOCH, "STARLINK-2"),
        (3, EPOCH, "ISS"),
        (4, EPOCH, "TERRA"),
        (5, EPOCH - pd.Timedelta(hours=6), "AQUA"),  # SOCRATES used an older set for this one
        (6, EPOCH, "TANDEM-A"),
        (7, EPOCH, "TANDEM-B"),
    ]
    return pd.DataFrame(rows, columns=["norad_id", "epoch", "object_name"])


def _socrates_row(a, b, tca, rng, speed, epoch_a=EPOCH, epoch_b=EPOCH):
    return {
        "NORAD_CAT_ID_1": a,
        "OBJECT_NAME_1": f"OBJ {a} [+]",
        "DSE_1": round((tca - epoch_a) / pd.Timedelta(days=1), 3),
        "NORAD_CAT_ID_2": b,
        "OBJECT_NAME_2": f"OBJ {b} [+]",
        "DSE_2": round((tca - epoch_b) / pd.Timedelta(days=1), 3),
        "TCA": tca,
        "TCA_RANGE": rng,
        "TCA_RELATIVE_SPEED": speed,
        "MAX_PROB": 0.0,
        "DILUTION": 0.0,
    }


def _ours_row(a, b, tca, miss, speed, colocated=False):
    return {"norad_a": a, "norad_b": b, "tca": tca, "miss_km": miss, "rel_speed_km_s": speed, "colocated": colocated}


@pytest.fixture
def comparison():
    h = lambda x: T0 + pd.Timedelta(hours=x)
    socrates = pd.DataFrame(
        [
            _socrates_row(3, 4, h(1), 1.0, 10.0),  # same inputs, we find it
            _socrates_row(3, 99, h(2), 2.0, 10.0),  # object 99 is not in our catalog
            _socrates_row(3, 5, h(3), 3.0, 10.0, epoch_b=EPOCH - pd.Timedelta(hours=12)),  # different set
            _socrates_row(3, 4, h(4), 4.9, 10.0),  # same inputs, we do not find it
            _socrates_row(6, 7, h(5), 0.3, 0.001),  # formation pair, per orbit
            _socrates_row(6, 7, h(6), 0.3, 0.001),
            _socrates_row(1, 3, h(0), 1.0, 10.0),  # anchors the window start
            _socrates_row(1, 3, h(10), 1.0, 10.0),  # anchors the window end
        ]
    )
    socrates["a"] = socrates[["NORAD_CAT_ID_1", "NORAD_CAT_ID_2"]].min(axis=1)
    socrates["b"] = socrates[["NORAD_CAT_ID_1", "NORAD_CAT_ID_2"]].max(axis=1)
    socrates.loc[socrates["NORAD_CAT_ID_2"] == 5, "DSE_2"] = round((h(3) - (EPOCH - pd.Timedelta(hours=12))) / pd.Timedelta(days=1), 3)
    ours = pd.DataFrame(
        [
            _ours_row(3, 4, h(1) + pd.Timedelta(milliseconds=3), 1.0004, 10.0),
            _ours_row(1, 2, h(2), 2.0, 9.0),  # Starlink pair SOCRATES omits
            _ours_row(3, 4, h(7), 3.0, 11.0),  # same inputs, SOCRATES lacks it
            _ours_row(6, 7, h(20), 0.29, 0.001, colocated=True),  # closest point outside the window
            _ours_row(1, 3, h(0), 1.0, 10.0),
            _ours_row(1, 3, h(10), 1.0, 10.0),
        ]
    )
    return compare(ours, socrates, _catalog(), None, edge=pd.Timedelta(minutes=5))


def test_epochs_recovered_from_days_since_epoch():
    s = pd.DataFrame([_socrates_row(3, 4, T0, 1.0, 10.0)])
    s["TCA"] = pd.to_datetime(s["TCA"], utc=True)
    epochs = socrates_epochs(s)
    assert abs(epochs[3] - EPOCH) < pd.Timedelta(seconds=90)


def test_reasons_for_socrates_events(comparison):
    s = comparison.socrates.set_index("TCA")
    h = lambda x: T0 + pd.Timedelta(hours=x)
    assert s.loc[h(1), "reason"] == "found"
    assert s.loc[h(1), "same_inputs"]
    assert s.loc[h(2), "reason"].startswith("object not in our catalog")
    assert s.loc[h(3), "reason"] == "different element sets"
    assert s.loc[h(4), "reason"] == "same inputs, not found"
    # Formation pair: our single row is outside the window, but the pair still matches.
    assert s.loc[h(5), "found"] and s.loc[h(6), "found"]
    # Window edges are trimmed.
    assert h(0) not in s.index and h(10) not in s.index


def test_reasons_for_our_events(comparison):
    o = comparison.ours.set_index("tca")
    h = lambda x: T0 + pd.Timedelta(hours=x)
    assert o.loc[h(1) + pd.Timedelta(milliseconds=3), "reason"] == "in SOCRATES"
    assert o.loc[h(2), "reason"] == "object absent from this SOCRATES run"  # object 2 never appears
    assert o.loc[h(7), "reason"] == "same inputs, not in SOCRATES"


def test_same_constellation_pairs_are_classified(comparison):
    o = comparison.ours
    starlink = o[(o["a"] == 1) & (o["b"] == 2)]
    assert starlink["same_family"].all()
    assert starlink["kind"].eq("Starlink-Starlink").all()
    iss_terra = o[(o["a"] == 3) & (o["b"] == 4)]
    assert not iss_terra["same_family"].any()
