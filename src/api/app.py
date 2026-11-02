"""Read-only FastAPI app over the published results bundle (build guide section 10).

It never downloads data, screens the catalog or trains anything. Every route is
under /v1, every response is a typed model carrying its source, every list is
paginated, and risk values use estimate wording, never "collision probability".
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
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
from src.api import views
from src.api.data import NoResults, ResultsStore
from src.config import Settings
from src.sim import service as simulator

log = logging.getLogger("perigee.api")

# Which code is running, so a deploy's smoke test can tell the new build from the old:
# Render sets RENDER_GIT_COMMIT; other hosts can set PERIGEE_CODE_COMMIT or ship a
# build_info.json next to src/.
BUILD_INFO = Path(__file__).resolve().parents[1] / "build_info.json"


def code_commit(path: Path = BUILD_INFO) -> str | None:
    for var in ("RENDER_GIT_COMMIT", "PERIGEE_CODE_COMMIT"):
        if value := os.environ.get(var, "").strip():
            return value
    try:
        return str(json.loads(path.read_text())["commit"])
    except (OSError, ValueError, KeyError):
        return None

MAX_LIMIT = views.MAX_LIMIT
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
    app.state.catalog = None

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

    def respond(request: Request, model: BaseModel) -> Response:
        """JSON with an ETag and cache headers; 304 when the client already has it."""
        st = app.state.store
        tag = hashlib.sha256(f"{st.meta['run_id'] if st else '-'}|{request.url.path}?{request.url.query}".encode()).hexdigest()[:20]
        etag = f'W/"{tag}"'
        headers = {"ETag": etag, "Cache-Control": f"public, max-age={CACHE_SECONDS}"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(model.model_dump_json(), media_type="application/json", headers=headers)

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
        return respond(request, views.meta(store()))

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
        return respond(request, s.ObjectPage(source=views.source(st), total=len(hits), limit=limit, offset=offset,
                                             items=[views.object_summary(r) for _, r in page.iterrows()]))

    @app.get("/v1/objects/{norad_id}", response_model=s.ObjectDetail)
    def object_detail(request: Request, norad_id: int):
        st = store()
        if norad_id not in st.objects.index:
            raise HTTPException(404, f"No object {norad_id} in this snapshot.")
        return respond(request, views.object_detail(norad_id, st))

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
        return respond(request, s.ConjunctionPage(source=views.source(st), total=len(selected), limit=limit, offset=offset,
                                                  items=[views.conjunction(r, st) for _, r in page.iterrows()]))

    @app.get("/v1/conjunctions/{conjunction_id}", response_model=s.ConjunctionDetail)
    def conjunction_detail(request: Request, conjunction_id: str):
        st = store()
        if conjunction_id not in st.conjunctions.index:
            raise HTTPException(404, "No such conjunction in the latest run.")
        return respond(request, views.conjunction_detail(st.conjunctions.loc[conjunction_id], st))

    @app.get("/v1/shells", response_model=s.Shells)
    def shells(request: Request):
        return respond(request, views.shells(store()))

    @app.get("/v1/shells/history", response_model=s.ShellHistory)
    def shells_history(request: Request):
        return respond(request, views.shells_history(store()))

    @app.get("/v1/model", response_model=s.ModelSummary)
    def model(request: Request):
        summary = views.model_summary(store())
        if summary is None:
            raise HTTPException(404, "No model was trained for this run; conjunctions are shown without estimates.")
        return respond(request, summary)

    @app.post("/v1/simulate", response_model=s.SimulateResponse)
    async def simulate_endpoint(req: s.SimulateRequest):
        st = store()
        try:
            with anyio.fail_after(SIMULATE_TIMEOUT_S):
                return await anyio.to_thread.run_sync(_run_simulation, req, st, limiter=sim_slots)
        except TimeoutError as exc:
            raise HTTPException(503, "The simulation took too long; try fewer points or times.") from exc

    def _run_simulation(req: s.SimulateRequest, st: ResultsStore) -> s.SimulateResponse:
        if app.state.catalog is None:
            app.state.catalog = simulator.Catalog(st.shell_objects(), st.snapshot_time)
        try:
            return s.SimulateResponse(**simulator.run(req.model_dump(), app.state.catalog))
        except simulator.SimulationInputError as exc:
            raise HTTPException(422, str(exc)) from exc

    return app

