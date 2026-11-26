"""Polite HTTP downloads from CelesTrak.

CelesTrak's usage policy says machine clients must stop querying immediately on
any non-200 response and report it to a human, or the IP gets firewalled. So:

* any HTTP status other than 200 (redirects included) raises and writes a STOP
  file; no further requests are made until a human deletes it. One exception:
  CelesTrak's own 403 "GP data has not updated since your last successful
  download" means the current update was already served to this IP (on a shared
  CI runner, to someone else) and clears when the data next updates. It raises
  NotUpdatedError instead, writes no STOP file, and the source is asked again
  only after its interval; NotUpdatedLog stops the job after a few in a row.
  Another: a server outage (502/503/504: CelesTrak's server or its front end is
  down) raises UnavailableError, and no request is made for a cool-down (the
  policy asks for at least 15 minutes after a 503); OutageLog writes the STOP
  file only after several outages in a row;
* only network-level failures (timeouts, dropped connections) are retried, with
  exponential backoff;
* every request is recorded in a fetch log so a source is never downloaded more
  often than its minimum interval, even when the download was rejected.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.config import Settings

log = logging.getLogger("perigee.ingest.download")

STOP_FILE_NAME = "STOP_CELESTRAK"
BACKOFF_BASE_S = 5.0

Fetcher = Callable[[str], bytes]


class HttpStatusError(RuntimeError):
    def __init__(self, url: str, status: int, message: str = "") -> None:
        super().__init__(f"HTTP {status} from {url}")
        self.url = url
        self.status = status
        # The start of the response body: CelesTrak explains a 403 in it (for
        # example "GP data has not updated since your last successful download").
        self.message = message


def _error_text(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read(2000).decode("utf-8", "replace").strip()[:500]
    except Exception:
        return ""


class NotUpdatedError(HttpStatusError):
    """CelesTrak's 403 for data already downloaded from this IP since its last update."""


NOT_UPDATED_MARK = "has not updated since your last successful download"


class UnavailableError(HttpStatusError):
    """A server outage (502/503/504): try again after a cool-down; no STOP file."""


OUTAGE_STATUSES = {502, 503, 504}


class CoolingDownError(RuntimeError):
    """No request made: the cool-down after a recent outage has not passed yet."""


class NetworkError(RuntimeError):
    pass


class StoppedError(RuntimeError):
    """Raised when a STOP file from an earlier refusal is still present."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None  # urllib then raises HTTPError for the 3xx response


def urllib_fetcher(user_agent: str, timeout_s: float) -> Fetcher:
    opener = urllib.request.build_opener(_NoRedirect)

    def fetch(url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": user_agent})
        try:
            with opener.open(request, timeout=timeout_s) as response:
                if response.status != 200:
                    raise HttpStatusError(url, response.status)
                body: bytes = response.read()
                return body
        except urllib.error.HTTPError as exc:
            raise HttpStatusError(url, exc.code, _error_text(exc)) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise NetworkError(f"{url}: {exc}") from exc

    return fetch


class PoliteClient:
    def __init__(
        self,
        settings: Settings,
        fetcher: Fetcher | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.fetcher = fetcher or urllib_fetcher(settings.user_agent, settings.http_timeout_s)
        self.sleep = sleep
        self.clock = clock
        self.stop_file = settings.state_dir / STOP_FILE_NAME
        self.outages = OutageLog(settings.state_dir)

    def check(self) -> None:
        """Raise if no request may be made now (a STOP file, or an outage cool-down)."""
        if self.stop_file.exists():
            raise StoppedError(
                f"{self.stop_file} exists from an earlier refused request. "
                "Investigate it, then delete the file to resume downloads."
            )
        until = self.outages.until()
        if until is not None and self.clock() < until:
            raise CoolingDownError(f"CelesTrak was unavailable; no requests until {until.isoformat(timespec='minutes')}")

    def get(self, url: str) -> bytes:
        self.check()
        attempts = max(1, self.settings.http_max_attempts)
        for attempt in range(1, attempts + 1):
            try:
                body = self.fetcher(url)
                log.info("downloaded", extra={"url": url, "bytes": len(body)})
                self.outages.clear()
                return body
            except HttpStatusError as exc:
                if exc.status == 403 and NOT_UPDATED_MARK in exc.message:
                    log.warning(
                        "celestrak: not updated since the last download from this IP; skipped until its next update",
                        extra={"url": url, "celestrak_message": exc.message},
                    )
                    raise NotUpdatedError(exc.url, exc.status, exc.message) from exc
                if exc.status in OUTAGE_STATUSES:
                    count = self.outages.add(self.clock() + timedelta(minutes=self.settings.outage_cooldown_min))
                    if count < self.settings.max_outages:
                        log.warning(
                            "celestrak unavailable; no requests until the cool-down passes",
                            extra={"url": url, "status": exc.status, "outages_in_a_row": count},
                        )
                        raise UnavailableError(exc.url, exc.status, exc.message) from exc
                    exc = HttpStatusError(
                        exc.url,
                        exc.status,
                        f"{count} outages in a row (HTTP {exc.status}): check that celestrak.org is up. "
                        f"Last answer: {exc.message}",
                    )
                self._write_stop_file(exc)
                log.error(
                    "celestrak refused request; all downloads stopped",
                    extra={"url": url, "status": exc.status, "stop_file": str(self.stop_file)},
                )
                raise
            except NetworkError as exc:
                if attempt == attempts:
                    log.error("network error, giving up", extra={"url": url, "error": str(exc)})
                    raise
                delay = BACKOFF_BASE_S * 2 ** (attempt - 1)
                log.warning(
                    "network error, backing off",
                    extra={"url": url, "attempt": attempt, "delay_s": delay, "error": str(exc)},
                )
                self.sleep(delay)
        raise AssertionError("unreachable")

    def stop(self, url: str, status: int, message: str) -> None:
        """Stop all downloads for a human, as a refusal does."""
        self._write_stop_file(HttpStatusError(url, status, message))

    def _write_stop_file(self, exc: HttpStatusError) -> None:
        self.stop_file.parent.mkdir(parents=True, exist_ok=True)
        self.stop_file.write_text(
            json.dumps(
                {
                    "url": exc.url,
                    "status": exc.status,
                    "message": exc.message,
                    "at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "action": "Check the URL and CelesTrak's usage policy, then delete this file.",
                },
                indent=2,
            )
        )


class NotUpdatedLog:
    """How many "not updated" 403s in a row each source has had (state/not_updated.json)."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "not_updated.json"

    def _load(self) -> dict[str, int]:
        if not self.path.exists():
            return {}
        data: dict[str, int] = json.loads(self.path.read_text())
        return data

    def _save(self, data: dict[str, int]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True))

    def add(self, source: str) -> int:
        data = self._load()
        data[source] = data.get(source, 0) + 1
        self._save(data)
        return data[source]

    def clear(self, source: str) -> None:
        data = self._load()
        if data.pop(source, None) is not None:
            self._save(data)


class OutageLog:
    """Server outages in a row and the end of the cool-down after the last (state/outage.json)."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "outage.json"

    def _load(self) -> dict[str, str | int]:
        if not self.path.exists():
            return {}
        data: dict[str, str | int] = json.loads(self.path.read_text())
        return data

    def until(self) -> datetime | None:
        raw = self._load().get("until")
        return datetime.fromisoformat(str(raw)) if raw else None

    def add(self, until: datetime) -> int:
        count = int(self._load().get("count", 0)) + 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"count": count, "until": until.isoformat()}, indent=2))
        return count

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class FetchLog:
    """When each source was last requested, so polling never exceeds policy."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "fetch_log.json"

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        data: dict[str, str] = json.loads(self.path.read_text())
        return data

    def last_attempt(self, source: str) -> datetime | None:
        raw = self._load().get(source)
        return datetime.fromisoformat(raw) if raw else None

    def is_due(self, source: str, min_interval_h: float, now: datetime) -> bool:
        last = self.last_attempt(source)
        return last is None or now - last >= timedelta(hours=min_interval_h)

    def record(self, source: str, when: datetime) -> None:
        data = self._load()
        data[source] = when.isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        tmp.replace(self.path)
