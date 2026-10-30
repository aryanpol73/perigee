"""Polite HTTP downloads from CelesTrak.

CelesTrak's usage policy says machine clients must stop querying immediately on
any non-200 response and report it to a human, or the IP gets firewalled. So:

* any HTTP status other than 200 (redirects included) raises and writes a STOP
  file; no further requests are made until a human deletes it;
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
    def __init__(self, url: str, status: int) -> None:
        super().__init__(f"HTTP {status} from {url}")
        self.url = url
        self.status = status


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
            raise HttpStatusError(url, exc.code) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise NetworkError(f"{url}: {exc}") from exc

    return fetch


class PoliteClient:
    def __init__(
        self,
        settings: Settings,
        fetcher: Fetcher | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.fetcher = fetcher or urllib_fetcher(settings.user_agent, settings.http_timeout_s)
        self.sleep = sleep
        self.stop_file = settings.state_dir / STOP_FILE_NAME

    def get(self, url: str) -> bytes:
        if self.stop_file.exists():
            raise StoppedError(
                f"{self.stop_file} exists from an earlier refused request. "
                "Investigate it, then delete the file to resume downloads."
            )
        attempts = max(1, self.settings.http_max_attempts)
        for attempt in range(1, attempts + 1):
            try:
                body = self.fetcher(url)
                log.info("downloaded", extra={"url": url, "bytes": len(body)})
                return body
            except HttpStatusError as exc:
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

    def _write_stop_file(self, exc: HttpStatusError) -> None:
        self.stop_file.parent.mkdir(parents=True, exist_ok=True)
        self.stop_file.write_text(
            json.dumps(
                {
                    "url": exc.url,
                    "status": exc.status,
                    "at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "action": "Check the URL and CelesTrak's usage policy, then delete this file.",
                },
                indent=2,
            )
        )


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
