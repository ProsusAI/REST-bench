# Copyright © 2026 MIH AI B.V.
# Licensed under the Apache License, Version 2.0.
# See LICENSE file in the project root for full license information.

"""CLI-side helper that pushes simulation events to the dashboard server.

All HTTP work goes through `post()`, which never raises so a dead or
missing dashboard cannot break the simulation. Failures are logged to
stderr at WARN level (still non-blocking, but visible) — set
`REST_DASHBOARD_DISABLE=1` to suppress all dashboard activity (tests).
`ensure_running()` spawns the server as a detached background process on
first use.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from typing import Any

from .dashboard import GAME_DIR, PID_PATH, DEFAULT_PORT

POST_TIMEOUT_SECONDS = 0.5
SPAWN_WAIT_SECONDS = 2.0

_logger = logging.getLogger("rest_sim.dashboard_client")
if not _logger.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("[dashboard] %(levelname)s: %(message)s"))
    _logger.addHandler(_h)
    _logger.setLevel(logging.WARNING)


def _disabled() -> bool:
    return os.environ.get("REST_DASHBOARD_DISABLE", "").strip() not in ("", "0", "false", "False")


def _port() -> int:
    try:
        return int(os.environ.get("REST_DASHBOARD_PORT", str(DEFAULT_PORT)))
    except ValueError:
        return DEFAULT_PORT


def _base_url() -> str:
    return f"http://127.0.0.1:{_port()}"


def _pid_alive() -> bool:
    if not os.path.exists(PID_PATH):
        return False
    try:
        with open(PID_PATH) as f:
            pid = int(f.read().strip() or "0")
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _server_responsive() -> bool:
    try:
        with urllib.request.urlopen(_base_url() + "/healthz",
                                     timeout=POST_TIMEOUT_SECONDS) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_running(open_browser: bool = True) -> bool:
    """Start the dashboard server if it isn't already.

    Returns True if the server is reachable after this call, False otherwise.
    Never raises.
    """
    if _disabled():
        return False
    try:
        if _pid_alive() and _server_responsive():
            return True
        # Stale PID file: clean it up before respawning.
        if os.path.exists(PID_PATH) and not _pid_alive():
            try:
                os.remove(PID_PATH)
            except OSError:
                pass
        os.makedirs(GAME_DIR, exist_ok=True)
        log_path = os.path.join(GAME_DIR, "dashboard.log")
        log = open(log_path, "ab", buffering=0)
        cmd = [sys.executable, "-m", "rest_sim.dashboard",
               "--port", str(_port())]
        if open_browser:
            cmd.append("--open-browser")
        kwargs: dict[str, Any] = {
            "stdout": log,
            "stderr": log,
            "stdin": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "posix":
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(cmd, **kwargs)
        deadline = time.time() + SPAWN_WAIT_SECONDS
        while time.time() < deadline:
            if _server_responsive():
                return True
            time.sleep(0.1)
        return False
    except Exception:
        return False


def post(event: str, payload: dict) -> None:
    """POST a JSON event to /events/<event>. Never raises, never blocks long.

    Failures (server down, timeout, refused connection, etc.) are logged
    at WARN level — the simulation must not depend on the dashboard, but
    silent failures used to make telemetry breakage invisible.
    """
    if _disabled():
        return
    try:
        data = json.dumps(payload, default=str).encode("utf-8")
        req = urllib.request.Request(
            f"{_base_url()}/events/{event}",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=POST_TIMEOUT_SECONDS).read()
    except Exception as e:
        _logger.warning("post(event=%s) failed: %s", event, e)
