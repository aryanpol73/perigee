"""Score live close approaches with the trained model (build guide 8.5).

The model was trained on approaches predicted up to 3 days ahead, excluding
same-constellation and co-located pairs. Rows outside that scope get no score
(the API shows "outside model scope"), never an extrapolated one. Features come
from the same code as in training, using only data published by the snapshot.

The update-history features need 30 days of element sets before the snapshot.
`history_days` records how much there was; below that the score is flagged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from src.ml.dataset import geometry_at_tca
from src.ml.features import FEATURES, build_features

log = logging.getLogger("perigee.ml.score")

MODEL_HORIZON_H = 72.0
# Measured in docs/history-sensitivity.md: 15 days of 6-hourly snapshots score like
# the full history, 7 days nearly so, a single snapshot far lower. 14 days is the bar.
FULL_HISTORY_DAYS = 14.0


@dataclass
class Model:
    version: str
    booster: lgb.Booster
    calibrator_x: np.ndarray
    calibrator_y: np.ndarray
    results: dict

    def probability(self, raw: np.ndarray) -> np.ndarray:
        # Isotonic regression predicts by linear interpolation, clipped at the ends.
        return np.interp(raw, self.calibrator_x, self.calibrator_y)

    @property
    def operating_threshold(self) -> float:
        """The validation operating point, as a calibrated probability."""
        raw = float(self.results["models"]["lightgbm"]["operating_threshold"])
        return float(self.probability(np.array([raw]))[0])


def load_model(models_dir: Path, version: str | None = None) -> Model | None:
    """The requested model version, or the most recently trained one; None if there is none."""
    if version is not None:
        path = models_dir / version
    else:
        runs = [p for p in models_dir.glob("*") if (p / "results.json").exists()]
        if not runs:
            return None
        path = max(runs, key=lambda p: (p / "results.json").stat().st_mtime)
    if not (path / "lightgbm.txt").exists():
        return None
    cal = json.loads((path / "calibrator.json").read_text())
    return Model(
        version=path.name,
        booster=lgb.Booster(model_file=str(path / "lightgbm.txt")),
        calibrator_x=np.asarray(cal["x"], dtype=float),
        calibrator_y=np.asarray(cal["y"], dtype=float),
        results=json.loads((path / "results.json").read_text()),
    )


def score_events(
    events: pd.DataFrame,
    catalog: pd.DataFrame,
    satcat: pd.DataFrame | None,
    stats: pd.DataFrame,
    space_weather: pd.DataFrame | None,
    start: pd.Timestamp,
    model: Model,
    explain: bool = False,
) -> pd.DataFrame:
    """events plus in_model_scope, raw_score and risk_probability_estimate (NaN outside scope).

    explain=True also adds top_reasons (JSON: three largest SHAP contributions).
    """
    out = events.copy()
    hours = (out["tca"] - start) / pd.Timedelta(hours=1)
    same = out["same_family"] if "same_family" in out else pd.Series(False, index=out.index)
    colocated = out["colocated"] if "colocated" in out else pd.Series(False, index=out.index)
    scope = (hours <= MODEL_HORIZON_H) & ~same.astype(bool) & ~colocated.astype(bool)
    out["in_model_scope"] = scope.to_numpy()
    out["raw_score"] = np.nan
    out["risk_probability_estimate"] = np.nan
    rows = out[scope].copy()
    if rows.empty:
        return out
    known = catalog[catalog["norad_id"].isin(pd.concat([rows["norad_a"], rows["norad_b"]]))]
    rows = pd.concat([rows, geometry_at_tca(rows, known, start)], axis=1)
    # Numeric even when a feature group is missing entirely (e.g. no update history yet).
    feats = build_features(rows, known, satcat, stats, space_weather, start).astype(float)
    raw = np.asarray(model.booster.predict(feats[FEATURES]))
    out.loc[rows.index, "raw_score"] = raw
    out.loc[rows.index, "risk_probability_estimate"] = model.probability(raw)
    if explain:
        # Top reasons: the features with the largest SHAP values (log-odds) for each row.
        contrib = np.asarray(model.booster.predict(feats[FEATURES], pred_contrib=True))[:, :-1]
        top = np.argsort(-np.abs(contrib), axis=1)[:, :3]
        out["top_reasons"] = None
        out.loc[rows.index, "top_reasons"] = [
            json.dumps([{"feature": FEATURES[j], "contribution": round(float(contrib[k, j]), 3)} for j in top[k]])
            for k in range(len(rows))
        ]
    log.info("scored events", extra={"in_scope": len(rows), "events": len(out), "model_version": model.version})
    return out
