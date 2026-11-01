"""Read-only FastAPI app over the published results bundle (build guide section 10).

It never downloads data, screens the catalog or trains anything. Every route is
under /v1, every response is a typed model carrying its source, every list is
paginated, and risk values use estimate wording, never "collision probability".
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections import OrderedDict, defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Literal

import anyio
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api import schemas as s
from src.api.data import NoResults, ResultsStore
from src.config import Settings
from src.orbits.frames import ecef_to_geodetic, gmst_rad, julian_dates
from src.risk.shells import ShellConfig, shell_index
from src.sim.breakup import Collision, as_catalog_objects, positions_at, simulate, spread

log = logging.getLogger("perigee.api")

# Written into the image by the deploy job, so a smoke test can tell which code runs.
BUILD_INFO = Path(__file__).resolve().parents[1] / "build_info.json"


def code_commit(path: Path = BUILD_INFO) -> str | None:
    try:
        return str(json.loads(path.read_text())["commit"])
    except (OSError, ValueError, KeyError):
        return None

MAX_LIMIT = 500
SIMULATE_TIMEOUT_S = 15.0
CACHE_ENTRIES = 4_096
CACHE_SECONDS = 300


class RateLimiter:
    """Sliding one-minute window per client IP (in memory; one process)."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def allow(self, client: str, bucket: str, limit: int | None = None) -> bool:
        now = time.monotonic()
        q = self.hits[(client, bucket)]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= (limit or self.per_minute):
            return False
        q.append(now)
        return True


class ResponseCache:
    """Caches successful GET /v1 JSON responses by URL.

    The bundle never changes while the process runs, so an identical request gets
    an identical answer. Sits inside the gzip layer (caches uncompressed bodies)
    and inside the rate limiter. Health and the orbit file are not cached here.
    """

    def __init__(self, app, entries: int = CACHE_ENTRIES) -> None:
        self.app = app
        self.entries = entries
        self.store: OrderedDict[str, tuple[int, list[tuple[bytes, bytes]], bytes]] = OrderedDict()

    async def __call__(self, scope, receive, send) -> None:
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or scope["method"] != "GET"
            or not path.startswith("/v1/")
            or path == "/v1/health"
            or path.startswith("/v1/orbits/")
        ):
            await self.app(scope, receive, send)
            return
        key = f"{path}?{scope.get('query_string', b'').decode()}"
        hit = self.store.get(key)
        if hit is not None:
            self.store.move_to_end(key)
            status, headers, body = hit
            etag = dict(headers).get(b"etag")
            wanted = dict(scope.get("headers", [])).get(b"if-none-match")
            if etag is not None and wanted == etag:
                await send({"type": "http.response.start", "status": 304, "headers": [h for h in headers if h[0] != b"content-length"]})
                await send({"type": "http.response.body", "body": b""})
                return
            # Outer layers (gzip) edit header lists in place: always hand them a copy.
            await send({"type": "http.response.start", "status": status, "headers": list(headers)})
            await send({"type": "http.response.body", "body": body})
            return

        start: dict = {}
        chunks: list[bytes] = []

        async def capture(message) -> None:
            if message["type"] == "http.response.start":
                start.update(message, headers=list(message.get("headers", [])))
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
            await send(message)

        await self.app(scope, receive, capture)
        if start.get("status") == 200:
            self.store[key] = (200, list(start.get("headers", [])), b"".join(chunks))
            if len(self.store) > self.entries:
                self.store.popitem(last=False)


def _error(status: int, code: str, message: str, request: Request) -> JSONResponse:
    rid = getattr(request.state, "request_id", "-")
    body = s.ErrorResponse(error=s.ErrorBody(code=code, message=message, request_id=rid))
    return JSONResponse(body.model_dump(), status_code=status, headers={"X-Request-ID": rid})


def _num(x) -> float | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) or pd.isna(x) else float(x)


def _utc(t: datetime) -> pd.Timestamp:
    ts = pd.Timestamp(t)
    return ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")


def create_app(settings: Settings | None = None, results_dir: Path | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    results_dir = results_dir or settings.results_dir
    app = FastAPI(
        title="Perigee API",
        version="1",
        description="Read-only estimates from public orbit data; not for operational use. "
        "Data from CelesTrak and Space-Track.org (USSPACECOM).",
    )
    if settings.sentry_dsn:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.sentry_environment,
            send_default_pii=False,
            traces_sample_rate=0.02,
        )
    limiter = RateLimiter(settings.api_rate_limit_per_min)
    deployed_commit = code_commit()
    try:
        app.state.store = ResultsStore.load(results_dir)
    except NoResults as exc:
        log.warning("no results bundle", extra={"reason": str(exc)})
        app.state.store = None
    app.state.shell_base = None

    sim_slots = anyio.CapacityLimiter(1)  # one simulation at a time, so reads are not starved
    app.add_middleware(ResponseCache)
    app.add_middleware(GZipMiddleware, minimum_size=1_000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.api_cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "If-None-Match"],
        expose_headers=["ETag", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        client = request.client.host if request.client else "unknown"
        path = request.url.path
        if path.startswith("/v1/") and path != "/v1/health":
            bucket, limit = ("simulate", settings.api_simulate_per_min) if path == "/v1/simulate" else ("read", None)
            if not limiter.allow(client, bucket, limit):
                return _error(429, "rate_limited", "Too many requests; try again in a minute.", request)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        log.info(
            "request",
            extra={
                "request_id": request.state.request_id,
                "method": request.method,
                "route": path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1_000, 1),
            },
        )
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 503: "unavailable", 405: "method_not_allowed"}.get(exc.status_code, "error")
        return _error(exc.status_code, code, str(exc.detail), request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", []))
        return _error(422, "invalid_request", f"{where}: {first.get('msg', 'invalid value')}", request)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"request_id": getattr(request.state, "request_id", "-")})
        return _error(500, "internal_error", "Something went wrong; quote the request id if you report it.", request)

    def store() -> ResultsStore:
        if app.state.store is None:
            raise HTTPException(503, "No results published yet.")
        return app.state.store

    def source(st: ResultsStore) -> s.Source:
        return s.Source(
            snapshot_id=st.meta["snapshot_id"],
            generated_at=st.meta["generated_at"],
            model_version=st.meta["model_version"],
            history_complete=bool(st.meta.get("history_complete", False)),
        )

    def respond(request: Request, model: BaseModel) -> Response:
        """JSON with an ETag and cache headers; 304 when the client already has it."""
        st = app.state.store
        tag = hashlib.sha256(f"{st.meta['run_id'] if st else '-'}|{request.url.path}?{request.url.query}".encode()).hexdigest()[:20]
        etag = f'W/"{tag}"'
        headers = {"ETag": etag, "Cache-Control": f"public, max-age={CACHE_SECONDS}"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(model.model_dump_json(), media_type="application/json", headers=headers)

    def obj_summary(row: pd.Series) -> s.ObjectSummary:
        return s.ObjectSummary(
            norad_id=int(row["norad_id"]),
            name=row["name"],
            object_type=row["object_type"],
            rcs_size=row["rcs_size"] if isinstance(row["rcs_size"], str) else None,
            owner=row["owner"] if isinstance(row["owner"], str) else None,
            regime=row["regime"] if isinstance(row["regime"], str) else None,
            perigee_km=float(row["perigee_km"]),
            apogee_km=float(row["apogee_km"]),
            inclination_deg=float(row["inclination_deg"]),
            epoch_age_days=float(row["epoch_age_h"]) / 24,
        )

    def conj_model(row: pd.Series, st: ResultsStore) -> s.Conjunction:
        p = _num(row["risk_probability_estimate"])
        op = st.meta.get("operating_probability")
        return s.Conjunction(
            id=row["id"],
            tca=row["tca"],
            hours_to_tca=float(row["hours_to_tca"]),
            miss_distance_km=float(row["miss_distance_km"]),
            relative_speed_kms=float(row["relative_speed_kms"]),
            risk_probability_estimate=p,
            in_model_scope=bool(row["in_model_scope"]),
            above_operating_point=None if p is None or op is None else p >= op,
            estimate_status="none" if p is None else "standard" if st.meta.get("history_complete", False) else "provisional",
            objects=[
                s.OrbitedObject(norad_id=int(row["norad_a"]), name=row["name_a"], object_type=row["type_a"], epoch_age_days=float(row["epoch_age_a_days"])),
                s.OrbitedObject(norad_id=int(row["norad_b"]), name=row["name_b"], object_type=row["type_b"], epoch_age_days=float(row["epoch_age_b_days"])),
            ],
        )

    # --- Routes -------------------------------------------------------------------

    @app.get("/v1/health", response_model=s.Health, responses={503: {"model": s.Health}})
    def health():
        st = app.state.store
        if st is None:
            raise HTTPException(503, "No results published yet.")
        age = (pd.Timestamp.now(tz="UTC") - st.snapshot_time) / pd.Timedelta(hours=1)
        body = s.Health(
            status="ok" if age <= settings.api_max_data_age_h else "stale",
            data_age_hours=round(float(age), 2),
            max_data_age_hours=settings.api_max_data_age_h if np.isfinite(settings.api_max_data_age_h) else None,
            snapshot_id=st.meta["snapshot_id"],
            model_version=st.meta["model_version"],
            code_commit=deployed_commit,
        )
        return JSONResponse(body.model_dump(), status_code=200 if body.status == "ok" else 503)

    @app.get("/v1/meta", response_model=s.Meta)
    def meta(request: Request):
        st = store()
        m = st.meta
        return respond(
            request,
            s.Meta(
                snapshot_id=m["snapshot_id"],
                snapshot_time=m["snapshot_time"],
                generated_at=m["generated_at"],
                commit=m["commit"],
                object_count=m["object_count"],
                conjunction_count=m["conjunction_count"],
                scored_conjunctions=m["scored_conjunctions"],
                same_constellation_left_out=m["conjunction_counts"]["same_constellation_left_out"],
                model_version=m["model_version"],
                operating_probability=m["operating_probability"],
                history_complete=m["history_complete"],
                history_days_for_features=m["history_days_for_features"],
                orbit_file_url=f"/v1/orbits/{m['orbit_file']}",
                disclaimer=m["disclaimer"],
                sources=m["sources"],
            ),
        )

    @app.get("/v1/orbits/{name}", include_in_schema=False)
    def orbits(name: str):
        st = store()
        if name != st.meta["orbit_file"]:
            raise HTTPException(404, "Unknown orbit file; ask /v1/meta for the current one.")
        # Pre-compressed and content-hashed: the browser decompresses it and may cache it for good.
        return Response(
            st.orbit_file().read_bytes(),
            media_type="application/json",
            headers={"Content-Encoding": "gzip", "Cache-Control": "public, max-age=31536000, immutable"},
        )

    @app.get("/v1/objects", response_model=s.ObjectPage)
    def objects(
        request: Request,
        object_type: Literal["PAY", "DEB", "R/B", "UNK"] | None = None,
        min_alt_km: float | None = Query(None, ge=0, le=500_000),
        max_alt_km: float | None = Query(None, ge=0, le=500_000),
        q: str | None = Query(None, min_length=1, max_length=64, description="Name substring or NORAD id"),
        limit: int = Query(50, ge=1, le=MAX_LIMIT),
        offset: int = Query(0, ge=0, le=1_000_000),
    ):
        st = store()
        o = st.objects
        mask = pd.Series(True, index=o.index)
        if object_type:
            mask &= o["object_type"] == object_type
        if min_alt_km is not None:
            mask &= o["apogee_km"] >= min_alt_km
        if max_alt_km is not None:
            mask &= o["perigee_km"] <= max_alt_km
        if q:
            mask &= (o["norad_id"].astype(str) == q.strip()) | o["name"].fillna("").str.contains(q.strip(), case=False, regex=False)
        hits = o[mask]  # objects are sorted by norad_id at load
        page = hits.iloc[offset : offset + limit]
        return respond(request, s.ObjectPage(source=source(st), total=len(hits), limit=limit, offset=offset,
                                             items=[obj_summary(r) for _, r in page.iterrows()]))

    @app.get("/v1/objects/{norad_id}", response_model=s.ObjectDetail)
    def object_detail(request: Request, norad_id: int):
        st = store()
        if norad_id not in st.objects.index:
            raise HTTPException(404, f"No object {norad_id} in this snapshot.")
        row = st.objects.loc[norad_id]
        c = st.conjunctions
        mine = c[(c["norad_a"] == norad_id) | (c["norad_b"] == norad_id)].sort_values("tca").head(MAX_LIMIT)
        base = obj_summary(row).model_dump()
        return respond(
            request,
            s.ObjectDetail(
                **base,
                source=source(st),
                object_id=row["object_id"],
                launch_date=None if pd.isna(row["launch_date"]) else row["launch_date"],
                ops_status_code=row["ops_status_code"] if isinstance(row["ops_status_code"], str) else None,
                period_min=float(row["period_min"]),
                eccentricity=float(row["eccentricity"]),
                epoch=row["epoch"],
                conjunctions=[conj_model(r, st) for _, r in mine.iterrows()],
            ),
        )

    @app.get("/v1/conjunctions", response_model=s.ConjunctionPage)
    def conjunctions(
        request: Request,
        min_probability: float | None = Query(None, ge=0, le=1),
        max_miss_km: float | None = Query(None, gt=0, le=50),
        tca_from: datetime | None = None,
        tca_to: datetime | None = None,
        object_type: Literal["PAY", "DEB", "R/B", "UNK"] | None = None,
        norad_id: int | None = Query(None, ge=1),
        scored_only: bool = False,
        sort: Literal["probability", "tca", "miss"] = "probability",
        limit: int = Query(50, ge=1, le=MAX_LIMIT),
        offset: int = Query(0, ge=0, le=1_000_000),
    ):
        st = store()
        c = st.conjunctions
        mask = np.ones(len(c), dtype=bool)
        if min_probability is not None:
            mask &= (c["risk_probability_estimate"] >= min_probability).to_numpy()
        if max_miss_km is not None:
            mask &= (c["miss_distance_km"] <= max_miss_km).to_numpy()
        if tca_from is not None:
            mask &= (c["tca"] >= _utc(tca_from)).to_numpy()
        if tca_to is not None:
            mask &= (c["tca"] <= _utc(tca_to)).to_numpy()
        if object_type:
            mask &= ((c["type_a"] == object_type) | (c["type_b"] == object_type)).to_numpy()
        if norad_id is not None:
            mask &= ((c["norad_a"] == norad_id) | (c["norad_b"] == norad_id)).to_numpy()
        if scored_only:
            mask &= c["risk_probability_estimate"].notna().to_numpy()
        order = st.orders[sort]
        selected = order[mask[order]]
        page = c.iloc[selected[offset : offset + limit]]
        return respond(request, s.ConjunctionPage(source=source(st), total=len(selected), limit=limit, offset=offset,
                                                  items=[conj_model(r, st) for _, r in page.iterrows()]))

    @app.get("/v1/conjunctions/{conjunction_id}", response_model=s.ConjunctionDetail)
    def conjunction_detail(request: Request, conjunction_id: str):
        st = store()
        if conjunction_id not in st.conjunctions.index:
            raise HTTPException(404, "No such conjunction in the latest run.")
        row = st.conjunctions.loc[conjunction_id]
        base = conj_model(row, st)
        reasons = json.loads(row["top_reasons"]) if isinstance(row["top_reasons"], str) else []
        ages = sorted([row["epoch_age_a_days"], row["epoch_age_b_days"]])
        note = (
            f"Orbit data for the two objects is {ages[0]:.1f} and {ages[1]:.1f} days old at the snapshot. Public orbit "
            f"data is accurate to roughly a kilometre and the error grows with age, so a predicted "
            f"{row['miss_distance_km']:.2f} km miss could easily be several kilometres either way."
        )
        if not base.in_model_scope:
            note += " This approach is outside the model's scope (more than 3 days ahead), so it has no estimate."
        elif not st.meta.get("history_complete", False):
            note += " " + s.PROVISIONAL_NOTE
        objs = [obj_summary(st.objects.loc[int(n)]) for n in (row["norad_a"], row["norad_b"])]
        return respond(
            request,
            s.ConjunctionDetail(**base.model_dump(), source=source(st), object_details=objs,
                                top_reasons=[s.Reason(**r) for r in reasons], confidence_note=note),
        )

    @app.get("/v1/shells", response_model=s.Shells)
    def shells(request: Request):
        st = store()
        bands = [
            s.ShellBand(
                alt_lo_km=r.alt_lo_km, alt_hi_km=r.alt_hi_km, objects=r.objects, density_per_km3=r.density_per_km3,
                mean_rel_speed_km_s=_num(r.mean_rel_speed_km_s), risk_index=r.risk_index, risk_index_weighted=r.risk_index_weighted,
            )
            for r in st.shells.itertuples()
        ]
        return respond(request, s.Shells(source=source(st), bands=bands))

    @app.get("/v1/shells/history", response_model=s.ShellHistory)
    def shells_history(request: Request):
        st = store()
        runs = [
            s.ShellRun(
                run_id=str(run_id),
                source=str(g["source"].iloc[0]),
                bands=[{"alt_lo_km": float(r.alt_lo_km), "risk_index": float(r.risk_index), "risk_index_weighted": float(r.risk_index_weighted)} for r in g.itertuples()],
            )
            for run_id, g in st.shells_history.groupby("run_id", sort=True)
        ]
        return respond(request, s.ShellHistory(runs=runs))

    @app.get("/v1/model", response_model=s.ModelSummary)
    def model(request: Request):
        st = store()
        if not st.model:
            raise HTTPException(404, "No model was trained for this run; conjunctions are shown without estimates.")
        return respond(request, s.ModelSummary(**st.model))

    @app.post("/v1/simulate", response_model=s.SimulateResponse)
    async def simulate_endpoint(req: s.SimulateRequest):
        st = store()
        try:
            with anyio.fail_after(SIMULATE_TIMEOUT_S):
                return await anyio.to_thread.run_sync(_run_simulation, req, st, limiter=sim_slots)
        except TimeoutError as exc:
            raise HTTPException(503, "The simulation took too long; try fewer points or times.") from exc

    def _run_simulation(req: s.SimulateRequest, st: ResultsStore) -> s.SimulateResponse:
        if any(t < 0 or t > 8_760 for t in req.times_h):
            raise HTTPException(422, "times_h values must be between 0 and 8760 hours.")
        collision = Collision(
            altitude_km=req.altitude_km, inclination_deg=req.inclination_deg, target_mass_kg=req.target_mass_kg,
            impactor_mass_kg=req.impactor_mass_kg, rel_speed_km_s=req.rel_speed_km_s, target_kind=req.target_kind,
            impactor_kind=req.impactor_kind, seed=req.seed, max_fragments=5_000,
        )
        b = simulate(collision)
        frags = b.fragments
        rng = np.random.default_rng(req.seed)
        pick = rng.choice(len(frags), min(req.max_points, len(frags)), replace=False) if len(frags) else np.array([], int)
        sub = frags.iloc[np.sort(pick)]
        t0 = st.snapshot_time
        frames = []
        for hours in sorted(req.times_h):
            r = positions_at(sub, hours) if len(sub) else np.zeros((0, 3))
            jd, fr = julian_dates([t0 + pd.Timedelta(hours=hours)])
            g = float(gmst_rad(jd, fr)[0])
            rot = np.array([[np.cos(g), np.sin(g), 0], [-np.sin(g), np.cos(g), 0], [0, 0, 1]])
            lat, lon, alt = ecef_to_geodetic(r @ rot.T) if len(r) else (np.array([]),) * 3
            spr = spread(frags, hours) if len(frags) else {"along_track_coverage": 0.0, "node_coverage": 0.0}
            frames.append(
                s.SimFrame(
                    hours=hours,
                    along_track_coverage=spr["along_track_coverage"],
                    node_coverage=spr["node_coverage"],
                    points=[(round(float(a), 3), round(float(o), 3), round(float(h), 1)) for a, o, h in zip(lat, lon, alt, strict=True)],
                )
            )
        if app.state.shell_base is None:
            app.state.shell_base = shell_index(st.shell_objects(), ShellConfig())
        base = app.state.shell_base
        after = shell_index(
            pd.concat([st.shell_objects(), as_catalog_objects(frags)], ignore_index=True),
            ShellConfig(),
            base["mean_rel_speed_km_s"].to_numpy(),
        )
        top = base["rate_per_year"].max()
        after_idx = 100 * after["rate_per_year"] / top if top > 0 else after["rate_per_year"] * 0
        # The bands whose index rises most relative to their own level (near-empty bands left out).
        populated = base["risk_index"] > 0.01
        change = (after_idx / base["risk_index"].where(populated) - 1).fillna(-np.inf)
        rows = change.nlargest(8).index.sort_values()
        summary = b.summary()
        return s.SimulateResponse(
            catastrophic=b.catastrophic,
            specific_energy_j_per_g=summary["specific_energy_j_per_g"],
            modelled_fragments=summary["modelled_fragments_over_min_lc"],
            simulated_fragments=len(frags),
            points_returned=len(sub),
            input_mass_kg=b.input_mass_kg,
            fragment_mass_kg=summary["fragment_mass_kg"],
            unresolved_mass_kg=b.unresolved_mass_kg,
            collision_time=t0.to_pydatetime(),
            frames=frames,
            what_if=[
                s.WhatIfBand(alt_lo_km=float(base.loc[i, "alt_lo_km"]), alt_hi_km=float(base.loc[i, "alt_hi_km"]),
                             risk_index_before=float(base.loc[i, "risk_index"]), risk_index_after=float(after_idx[i]))
                for i in rows
            ],
        )

    return app

