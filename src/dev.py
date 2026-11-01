"""One command for local development: the API and the web app together.

  python -m src.main dev

Serves data/results if a bundle has been published there, otherwise the small
fixture bundle committed in tests/fixtures/results (so a clean checkout runs).
Installs the web app's npm packages on first use. Ctrl-C stops both.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from src.config import Settings
from src.results.bundle import POINTER
from src.results.fixture import FIXTURE_DIR

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
API_PORT = 8000


def _wait_for_api(api: subprocess.Popen, timeout_s: float = 90.0) -> None:
    """Start the web app only once the API answers, so the first page load has data."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and api.poll() is None:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{API_PORT}/v1/meta", timeout=2):
                return
        except OSError:
            time.sleep(0.5)


def run_dev(settings: Settings) -> int:
    npm = shutil.which("npm")
    if npm is None:
        print("npm was not found. Install Node.js 20 or newer (https://nodejs.org), then run this again.")
        return 1
    if not (WEB / "node_modules").exists():
        print("Installing the web app's packages (first run only)...")
        subprocess.run([npm, "ci"], cwd=WEB, check=True)

    use_fixture = not (settings.results_dir / POINTER).exists()
    api_cmd = [sys.executable, "-m", "src.main", "api", "--port", str(API_PORT)] + (["--fixture"] if use_fixture else [])
    print(f"API on http://127.0.0.1:{API_PORT}/docs serving {'the fixture bundle' if use_fixture else settings.results_dir}")
    api = subprocess.Popen(api_cmd, cwd=ROOT)
    _wait_for_api(api)
    web = subprocess.Popen([npm, "run", "dev"], cwd=WEB)
    print("Web app on http://localhost:5173 (Ctrl-C stops both)")
    try:
        while api.poll() is None and web.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in (web, api):
            if p.poll() is None:
                p.terminate()
        for p in (web, api):
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
    return 0
