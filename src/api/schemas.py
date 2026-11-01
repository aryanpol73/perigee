"""Typed API contracts (build guide 10.1-10.5). Every response says where it came from."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DISCLAIMER = "Estimate from public orbit data; not for operational use."
SIM_LABEL = "Illustrative, simplified; not a prediction."


class Source(BaseModel):
    snapshot_id: str
    generated_at: datetime
    model_version: str | None


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class Health(BaseModel):
    status: Literal["ok", "stale"]
    data_age_hours: float
    max_data_age_hours: float
    snapshot_id: str
    model_version: str | None


class Meta(BaseModel):
    snapshot_id: str
    snapshot_time: datetime
    generated_at: datetime
    commit: str
    object_count: int
    conjunction_count: int
    scored_conjunctions: int
    same_constellation_left_out: int
    model_version: str | None
    operating_probability: float | None = Field(description="Validation operating point, as a probability estimate")
    history_complete: bool = Field(description="False when the update-history features had less than 25 days of data")
    history_days_for_features: float | None
    orbit_file_url: str
    disclaimer: str
    sources: str


class ObjectSummary(BaseModel):
    norad_id: int
    name: str | None
    object_type: str
    rcs_size: str | None
    owner: str | None
    regime: str | None
    perigee_km: float
    apogee_km: float
    inclination_deg: float
    epoch_age_days: float


class ObjectPage(BaseModel):
    source: Source
    total: int
    limit: int
    offset: int
    items: list[ObjectSummary]


class OrbitedObject(BaseModel):
    norad_id: int
    name: str | None
    object_type: str | None
    epoch_age_days: float


class Conjunction(BaseModel):
    id: str
    tca: datetime
    hours_to_tca: float
    miss_distance_km: float
    relative_speed_kms: float
    risk_probability_estimate: float | None = Field(
        description="Chance the approach still looks closer than 5 km once newer public data arrives; "
        "null outside the model's scope"
    )
    in_model_scope: bool
    above_operating_point: bool | None
    objects: list[OrbitedObject]


class ConjunctionPage(BaseModel):
    source: Source
    total: int
    limit: int
    offset: int
    items: list[Conjunction]
    disclaimer: str = DISCLAIMER


class Reason(BaseModel):
    feature: str
    contribution: float = Field(description="SHAP value in log-odds; positive raises the estimate")


class ConjunctionDetail(Conjunction):
    source: Source
    object_details: list[ObjectSummary]
    top_reasons: list[Reason]
    confidence_note: str
    disclaimer: str = DISCLAIMER


class ObjectDetail(ObjectSummary):
    source: Source
    object_id: str | None
    launch_date: datetime | None
    ops_status_code: str | None
    period_min: float
    eccentricity: float
    epoch: datetime
    conjunctions: list[Conjunction]


class ShellBand(BaseModel):
    alt_lo_km: float
    alt_hi_km: float
    objects: float
    density_per_km3: float
    mean_rel_speed_km_s: float | None
    risk_index: float
    risk_index_weighted: float


class Shells(BaseModel):
    source: Source
    bands: list[ShellBand]
    note: str = "Relative index (100 = riskiest band in this catalog), never a probability."


class ShellRun(BaseModel):
    run_id: str
    source: str
    bands: list[dict[str, float]]


class ShellHistory(BaseModel):
    runs: list[ShellRun]


class SimulateRequest(BaseModel):
    altitude_km: float = Field(800.0, ge=150, le=2_000)
    inclination_deg: float = Field(74.0, ge=0, le=180)
    target_mass_kg: float = Field(950.0, ge=1, le=10_000)
    impactor_mass_kg: float = Field(560.0, ge=1, le=10_000)
    rel_speed_km_s: float = Field(11.7, ge=0.1, le=16)
    target_kind: Literal["SC", "RB"] = "SC"
    impactor_kind: Literal["SC", "RB"] = "SC"
    seed: int = Field(42, ge=0, le=2**31 - 1)
    max_points: int = Field(1_000, ge=1, le=2_000)
    times_h: list[float] = Field(
        default_factory=lambda: [0.0833, 1, 6, 24, 168, 720, 2_160, 8_760],
        min_length=1,
        max_length=12,
        description="Moments after the collision to return positions for (hours, at most one year each)",
    )

    model_config = {"extra": "forbid"}


class SimFrame(BaseModel):
    hours: float
    along_track_coverage: float
    node_coverage: float
    points: list[tuple[float, float, float]] = Field(description="[lat deg, lon deg, altitude km] per fragment")


class WhatIfBand(BaseModel):
    alt_lo_km: float
    alt_hi_km: float
    risk_index_before: float
    risk_index_after: float


class SimulateResponse(BaseModel):
    label: str = SIM_LABEL
    catastrophic: bool
    specific_energy_j_per_g: float
    modelled_fragments: int = Field(description="NASA breakup model count of fragments 10 cm and larger")
    simulated_fragments: int
    points_returned: int
    input_mass_kg: float
    fragment_mass_kg: float
    unresolved_mass_kg: float
    collision_time: datetime
    frames: list[SimFrame]
    what_if: list[WhatIfBand]


class ModelMetrics(BaseModel):
    pr_auc: float
    pr_auc_ci95: list[float | None]
    alerts: int
    precision: float | None
    recall: float | None
    false_alarms_per_true: float | None
    brier: float | None = None


class PrecisionAtRecall(BaseModel):
    recall: float
    precision: float | None


class PrecisionAtTopK(BaseModel):
    k: int
    precision: float


class Tradeoff(BaseModel):
    precision_at_recall: list[PrecisionAtRecall]
    precision_at_top_k: list[PrecisionAtTopK]


class ModelSummary(BaseModel):
    model_version: str
    commit: str | None
    data_window: list[str] | None
    label: str
    scope: str
    test_counts: dict[str, int]
    metrics: dict[str, ModelMetrics]
    tradeoff: Tradeoff | None
    operating_probability: float
    lightgbm_beats_all_baselines: bool
    top_features: dict[str, float]
    limits: list[str]
    disclaimer: str = "Not for operational decisions."
