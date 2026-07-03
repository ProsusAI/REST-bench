# Copyright © 2026 MIH AI B.V.
# Licensed under the Apache License, Version 2.0.
# See LICENSE file in the project root for full license information.

"""Real-time dashboard server for the restaurant simulation.

Long-running stdlib HTTP server (no external deps). The CLI pushes events
via POST; browsers subscribe via Server-Sent Events. The server outlives
individual simulation runs — running `init` again resets the live view
and archives the prior run to game/dashboard_runs/<timestamp>.json.

Run directly:
    python -m rest_sim.dashboard [--port 8765] [--no-browser]

Or via CLI:
    python -m rest_sim dashboard
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
DATA_DIR = os.path.join(ROOT, "data")
GAME_DIR = os.environ.get("REST_GAME_DIR", os.path.join(ROOT, "game"))
STATE_PATH = os.path.join(GAME_DIR, "state.json")
PID_PATH = os.path.join(GAME_DIR, "dashboard.pid")
RUNS_DIR = os.path.join(GAME_DIR, "dashboard_runs")
INDEX_HTML_PATH = os.path.join(HERE, "dashboard_index.html")

RESTAURANT_SECTIONS = ("restaurant", "menu", "staff", "tables")

DEFAULT_PORT = int(os.environ.get("REST_DASHBOARD_PORT", "8765"))
HEARTBEAT_SECONDS = 15


class QuietHTTPServer(ThreadingHTTPServer):
    """Same as ThreadingHTTPServer but silences benign client disconnects.

    Browsers (and curl) routinely drop long-lived SSE connections without a
    clean close, which the stdlib server otherwise logs as a multi-page
    traceback. We swallow those specific exceptions so the log stays useful.
    `allow_reuse_address` lets us bind even when a prior process left the
    port in TIME_WAIT (common after SIGKILL during development).
    """

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


class RunBuffer:
    """In-memory record of the currently displayed simulation run.

    All access goes through `self.lock`. The server holds exactly one
    of these; on init the old buffer is archived and a fresh one starts.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.run_id: str | None = None
        self.init_payload: dict | None = None
        self.days: list[dict] = []
        self.decisions: list[dict] = []
        self.last_status: dict | None = None
        self.started_at: str | None = None

    def reset(self, init_payload: dict) -> None:
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.init_payload = init_payload
        self.days = []
        self.decisions = []
        self.last_status = None
        self.started_at = datetime.now(timezone.utc).isoformat()

    def snapshot(self) -> dict:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "init": self.init_payload,
            "days": self.days,
            "decisions": self.decisions,
            "status": self.last_status,
        }


class Broadcaster:
    """Fans out SSE events to every connected client queue."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.clients: list[queue.Queue] = []

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=256)
        with self.lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)

    def publish(self, event: str, data: dict) -> None:
        payload = {"event": event, "data": data}
        with self.lock:
            dead: list[queue.Queue] = []
            for q in self.clients:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                if q in self.clients:
                    self.clients.remove(q)


BUFFER = RunBuffer()
BROADCASTER = Broadcaster()


def _archive_current_run() -> str | None:
    """Persist the current buffer (if any) to disk; return the path or None."""
    snap = BUFFER.snapshot()
    if not snap["init"] and not snap["days"]:
        return None
    os.makedirs(RUNS_DIR, exist_ok=True)
    name = f"{snap['run_id'] or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path = os.path.join(RUNS_DIR, name)
    with open(path, "w") as f:
        json.dump(snap, f, default=str)
    return path


def _load_index_html() -> bytes:
    if not os.path.exists(INDEX_HTML_PATH):
        return b"<h1>dashboard_index.html missing</h1>"
    with open(INDEX_HTML_PATH, "rb") as f:
        return f.read()


def _load_json_file(path: str) -> Any:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {"error": f"failed to load {os.path.basename(path)}: {e}"}


def _build_restaurant_view() -> dict:
    """Bundle the static config files plus the dynamic state slices that
    the Explore tab cares about. Read fresh on each request — these files
    are tiny (a few KB total) and the user expects edits to show up."""
    out: dict[str, Any] = {"sections": {}}
    for name in RESTAURANT_SECTIONS:
        path = os.path.join(DATA_DIR, f"{name}.json")
        if os.path.exists(path):
            out["sections"][name] = _load_json_file(path)
    state_slice: dict[str, Any] = {}
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError):
            state = {}
        for key in (
            "inventory", "price_overrides", "menu_added", "menu_removed",
            "table_layout", "table_layout_changes", "active_promos",
            "staffing_override", "reservation_caps", "marketing_spend_by_day",
            "ingredient_price_multiplier", "weather_log", "incidents_log",
            "competitor_active", "loyalty_enabled", "starting_cash_eur",
            "cash_eur", "reputation", "satisfaction_ewma",
            "day_index", "total_days", "start_date", "seed",
        ):
            if key in state:
                state_slice[key] = state[key]
    out["state"] = state_slice
    return out


def _list_runs() -> list[dict]:
    if not os.path.isdir(RUNS_DIR):
        return []
    rows: list[dict] = []
    for name in sorted(os.listdir(RUNS_DIR), reverse=True):
        if not name.endswith(".json"):
            continue
        path = os.path.join(RUNS_DIR, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        rows.append({
            "name": name,
            "size_bytes": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        })
    return rows


class Handler(BaseHTTPRequestHandler):
    server_version = "RestDashboard/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        # Quiet by default; uncomment for debugging.
        # sys.stderr.write("[dash] " + format % args + "\n")
        pass

    # --- helpers -------------------------------------------------------------

    def _send_json(self, code: int, body: dict | list) -> None:
        data = json.dumps(body, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # --- routes --------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/" or path == "/index.html":
            body = _load_index_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/snapshot":
            with BUFFER.lock:
                self._send_json(200, BUFFER.snapshot())
            return
        if path == "/api/runs":
            self._send_json(200, _list_runs())
            return
        if path == "/api/restaurant":
            self._send_json(200, _build_restaurant_view())
            return
        if path.startswith("/api/runs/"):
            name = path[len("/api/runs/"):]
            if "/" in name or ".." in name or not name.endswith(".json"):
                self._send_json(400, {"error": "bad name"})
                return
            full = os.path.join(RUNS_DIR, name)
            if not os.path.exists(full):
                self._send_json(404, {"error": "not found"})
                return
            with open(full, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/events":
            self._serve_sse()
            return
        if path == "/healthz":
            self._send_json(200, {"ok": True})
            return
        self._send_json(404, {"error": "not found", "path": path})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        body = self._read_json()
        if path == "/events/init":
            archived = _archive_current_run()
            with BUFFER.lock:
                BUFFER.reset(body)
            BROADCASTER.publish("init", {"archived": archived, "init": body,
                                          "run_id": BUFFER.run_id})
            self._send_json(200, {"ok": True, "archived": archived,
                                   "run_id": BUFFER.run_id})
            return
        if path == "/events/advance":
            with BUFFER.lock:
                BUFFER.days.append(body)
                status = body.get("status")
                if status:
                    BUFFER.last_status = status
            BROADCASTER.publish("advance", body)
            self._send_json(200, {"ok": True})
            return
        if path == "/events/decision":
            with BUFFER.lock:
                BUFFER.decisions.append(body)
            BROADCASTER.publish("decision", body)
            self._send_json(200, {"ok": True})
            return
        if path == "/events/status":
            with BUFFER.lock:
                BUFFER.last_status = body
            BROADCASTER.publish("status", body)
            self._send_json(200, {"ok": True})
            return
        self._send_json(404, {"error": "not found", "path": path})

    # --- SSE -----------------------------------------------------------------

    def _serve_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = BROADCASTER.subscribe()
        try:
            with BUFFER.lock:
                snap = BUFFER.snapshot()
            self._sse_write("snapshot", snap)
            last_beat = time.time()
            while True:
                try:
                    item = q.get(timeout=1.0)
                    self._sse_write(item["event"], item["data"])
                except queue.Empty:
                    if time.time() - last_beat > HEARTBEAT_SECONDS:
                        try:
                            self.wfile.write(b": heartbeat\n\n")
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            break
                        last_beat = time.time()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            BROADCASTER.unsubscribe(q)

    def _sse_write(self, event: str, data: dict) -> None:
        try:
            payload = json.dumps(data, default=str)
        except Exception as e:
            payload = json.dumps({"error": "serialize_failed", "msg": str(e)})
        try:
            self.wfile.write(f"event: {event}\n".encode("utf-8"))
            for line in payload.split("\n"):
                self.wfile.write(f"data: {line}\n".encode("utf-8"))
            self.wfile.write(b"\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            raise


# --- lifecycle ---------------------------------------------------------------

def _write_pid() -> None:
    os.makedirs(GAME_DIR, exist_ok=True)
    with open(PID_PATH, "w") as f:
        f.write(str(os.getpid()))


def _clear_pid() -> None:
    try:
        if os.path.exists(PID_PATH):
            with open(PID_PATH) as f:
                pid = int(f.read().strip() or "0")
            if pid == os.getpid():
                os.remove(PID_PATH)
    except Exception:
        pass


def serve(port: int = DEFAULT_PORT, open_browser: bool = False) -> None:
    try:
        httpd = QuietHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        sys.stderr.write(f"[dashboard] cannot bind 127.0.0.1:{port}: {e}\n")
        sys.exit(0)
    _write_pid()

    def shutdown(*_args: Any) -> None:
        # httpd.shutdown() blocks until serve_forever notices, so we have to
        # call it from a side thread — calling it from the signal handler on
        # the main thread (which IS serve_forever) would deadlock.
        _clear_pid()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    if open_browser:
        threading.Thread(
            target=lambda: (time.sleep(0.3), webbrowser.open(f"http://127.0.0.1:{port}")),
            daemon=True,
        ).start()

    sys.stderr.write(f"[dashboard] listening on http://127.0.0.1:{port}\n")
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    finally:
        _clear_pid()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="rest_sim.dashboard")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--open-browser", action="store_true")
    args = p.parse_args(argv)
    serve(port=args.port, open_browser=args.open_browser)


if __name__ == "__main__":
    main()
