import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

from src.ingest.download import (
    FetchLog,
    HttpStatusError,
    NetworkError,
    PoliteClient,
    StoppedError,
    urllib_fetcher,
)
from tests.conftest import NOW, FakeFetcher


class _Handler(BaseHTTPRequestHandler):
    seen_user_agents: ClassVar[list[str]] = []

    def do_GET(self):
        self.seen_user_agents.append(self.headers.get("User-Agent", ""))
        if self.path == "/ok":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"A,B\n1,2\n")
        elif self.path == "/moved":
            self.send_response(301)
            self.send_header("Location", "/ok")
            self.end_headers()
        else:
            self.send_response(503)
            self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_urllib_fetcher_sends_user_agent(server):
    fetch = urllib_fetcher("Perigee-test/1.0", timeout_s=5)
    assert fetch(f"{server}/ok") == b"A,B\n1,2\n"
    assert _Handler.seen_user_agents[-1] == "Perigee-test/1.0"


def test_urllib_fetcher_does_not_follow_redirects(server):
    fetch = urllib_fetcher("Perigee-test/1.0", timeout_s=5)
    with pytest.raises(HttpStatusError) as exc:
        fetch(f"{server}/moved")
    assert exc.value.status == 301


def test_urllib_fetcher_raises_on_server_error(server):
    fetch = urllib_fetcher("Perigee-test/1.0", timeout_s=5)
    with pytest.raises(HttpStatusError) as exc:
        fetch(f"{server}/busy")
    assert exc.value.status == 503


def test_urllib_fetcher_network_failure():
    fetch = urllib_fetcher("Perigee-test/1.0", timeout_s=2)
    with pytest.raises(NetworkError):
        fetch("http://127.0.0.1:9/unreachable")


def test_refusal_writes_stop_file_and_blocks_later_requests(settings):
    fetcher = FakeFetcher({"u": HttpStatusError("u", 403), "v": b"data"})
    client = PoliteClient(settings, fetcher=fetcher, sleep=lambda s: None)
    with pytest.raises(HttpStatusError):
        client.get("u")
    assert client.stop_file.exists()
    assert '"status": 403' in client.stop_file.read_text()

    with pytest.raises(StoppedError):
        client.get("v")
    assert fetcher.calls == ["u"]  # no request after the refusal, and no retry of it


def test_network_errors_back_off_exponentially_then_succeed(settings):
    attempts = iter([NetworkError("reset"), NetworkError("timeout"), b"ok"])

    def flaky(url):
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    sleeps = []
    client = PoliteClient(settings, fetcher=flaky, sleep=sleeps.append)
    assert client.get("u") == b"ok"
    assert sleeps == [5.0, 10.0]


def test_network_errors_give_up_after_max_attempts(settings):
    fetcher = FakeFetcher({"u": NetworkError("down")})
    client = PoliteClient(settings, fetcher=fetcher, sleep=lambda s: None)
    with pytest.raises(NetworkError):
        client.get("u")
    assert len(fetcher.calls) == settings.http_max_attempts
    assert not client.stop_file.exists()


def test_fetch_log_enforces_minimum_interval(tmp_path):
    fetch_log = FetchLog(tmp_path)
    assert fetch_log.is_due("gp", 2.0, NOW)
    fetch_log.record("gp", NOW)
    assert not fetch_log.is_due("gp", 2.0, NOW + timedelta(hours=1, minutes=59))
    assert fetch_log.is_due("gp", 2.0, NOW + timedelta(hours=2))
    assert fetch_log.is_due("satcat", 12.0, NOW)
