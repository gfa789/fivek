"""Local HTTP application with testable lifecycle and short-lived database access."""
from __future__ import annotations

import hashlib
import json
import secrets
import socket
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import urlsplit

from garmin_api import GarminAuth, GarminError, GarminMFARequired
from .config import Config, local_today
from .coaching import validate_benchmark, validate_preferences
from .pipeline import PROCESSING_VERSION, atomic_text, calendar, refresh, report
from .store import BusyError, Store, refresh_active, now
from .sync import SyncCancelled
from .calendar_sync import CalendarError


class Application:
    def __init__(self, config: Config, auth_factory=GarminAuth.from_environment):
        self.config = config
        self.auth_factory = auth_factory
        self.auth = None
        self.token = secrets.token_urlsafe(32)
        self.gate = threading.RLock()
        self.cancel_event = threading.Event()
        self.state = {"busy": False, "mfa": False, "message": "Ready", "error": None}
        self.worker_thread = None
        self.calendar_thread = None
        self.calendar_pending = False

    def queue_calendar_sync(self):
        if not (self.config.data_dir / "calendar-credentials.bin").exists():
            return
        with self.gate:
            if self.calendar_thread is not None:
                self.calendar_pending = True
                return
            self.calendar_thread = threading.Thread(target=self._calendar_sync, daemon=True)
            self.calendar_thread.start()

    def _calendar_sync(self):
        from .calendar_sync import sync_calendar
        while True:
            try:
                sync_calendar(self.config, self.get_report())
            except Exception:
                store = None
                try:
                    store = Store(self.config.data_dir)
                    store.set_setting("calendar_sync_error", {"message": "Calendar sync needs a retry; the next automatic cycle will retry.", "failed_at": now()})
                except Exception:
                    pass  # Preserve the successful local save if diagnostics are busy.
                finally:
                    if store is not None:
                        store.close()
            with self.gate:
                if self.calendar_pending:
                    self.calendar_pending = False
                    continue
                self.calendar_thread = None
                return

    def update(self, **values):
        with self.gate:
            self.state.update(values)

    def status(self):
        store = Store(self.config.data_dir)
        try:
            sync = store.sync_status()
            last_report = store.db.execute("SELECT COALESCE(json_extract(payload,'$.generated_at'),generated_at) FROM reports ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            store.close()
        external_busy = refresh_active(self.config.data_dir)
        with self.gate:
            state = dict(self.state)
        if not external_busy and not state["busy"] and sync["run"] and sync["run"]["status"] == "running":
            sync["run"] = {**sync["run"], "status": "interrupted"}
            state["message"] = "Previous refresh was interrupted; refresh to resume saved checkpoints"
        if not external_busy and not state["busy"] and state["message"] == "Stopping after the current Garmin request":
            state["message"] = "Refresh cancelled; saved checkpoints retained" if sync["run"] and sync["run"]["status"] == "cancelled" else "Ready"
        return {**state, "as_of": local_today().isoformat(), "busy": state["busy"] or external_busy,
                "message": sync["run"]["progress"] if external_busy and sync["run"] else state["message"],
                "sync": sync, "report_generated_at": last_report[0] if last_report else None}

    def start_refresh(self, body: dict):
        code, full = body.get("code"), body.get("full", False)
        if code is not None and (not isinstance(code, str) or not code.strip() or len(code) > 32):
            raise ValueError("Enter a valid verification code")
        if type(full) is not bool:
            raise ValueError("full must be a boolean")
        with self.gate:
            if self.state["busy"] or refresh_active(self.config.data_dir):
                raise BusyError("Refresh already running")
            if code and (self.auth is None or not self.auth.mfa_pending):
                raise ValueError("Start refresh to request a verification code")
            self.cancel_event = threading.Event()
            self.state.update(busy=True, error=None, message="Connecting to Garmin")
            self.worker_thread = threading.Thread(target=self._refresh, args=(code, full), daemon=True)
            self.worker_thread.start()

    def _refresh(self, code, full):
        try:
            if self.auth is None:
                self.auth = self.auth_factory()
            api = self.auth.complete_mfa(code) if code else self.auth.login()
            if self.cancel_event.is_set():
                raise SyncCancelled()
            self.update(mfa=False)
            result = refresh(api, self.config, full=full, cancel=self.cancel_event,
                             progress=lambda message: self.update(message=message))
            status = result["sync"]["run"]["status"]
            self.queue_calendar_sync()
            self.update(message="Refresh complete" if status == "complete" else "Refresh finished with some unavailable data")
        except GarminMFARequired:
            if self.cancel_event.is_set():
                self.auth = None
                self.update(mfa=False, message="Verification cancelled", error=None)
            else:
                self.update(mfa=True, message="Enter your Garmin verification code")
        except SyncCancelled:
            self.update(mfa=False, message="Refresh cancelled; saved checkpoints retained", error=None)
        except (GarminError, ValueError, RuntimeError) as exc:
            self.update(error=str(exc), message="Refresh stopped; saved progress is retained")
        except Exception:
            self.update(error="Refresh could not complete. Saved data is retained.", message="Refresh stopped")
        finally:
            self.update(busy=False)

    def cancel(self):
        with self.gate:
            self.cancel_event.set()
            if self.state["mfa"] and not self.state["busy"]:
                self.auth = None
                self.state.update(mfa=False, message="Verification cancelled", error=None)
                return
        if refresh_active(self.config.data_dir):
            store = Store(self.config.data_dir)
            try:
                run = store.sync_status()["run"]
                if run and run["status"] == "running":
                    atomic_text(self.config.data_dir / "cancel.request", json.dumps({"run_id": run["id"]}))
            finally:
                store.close()
        if self.status()["busy"]:
            self.update(message="Stopping after the current Garmin request")

    def get_report(self):
        store = Store(self.config.data_dir)
        try:
            result = store.latest_report()
            if result and result.get("processing_version", 0) > PROCESSING_VERSION:
                return result
            outdated = (result is None or result["as_of"] != local_today().isoformat()
                        or result.get("configuration") != self.config.report_settings()
                        or result.get("processing_version") != PROCESSING_VERSION
                        or result.get("source_revision", -1) != store.setting("data_revision", 0))
            if outdated and not refresh_active(self.config.data_dir):
                try:
                    result = report(store, self.config)
                except BusyError:
                    if result is None:
                        raise
            if result is None:
                raise BusyError("Your first report is being prepared. Please retry shortly.")
            return result
        finally:
            store.close()

    def save_feedback(self, body: dict):
        try:
            day = date.fromisoformat(body["date"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Choose a valid check-in date") from None
        if day > local_today():
            raise ValueError("Check-in date cannot be in the future")
        store = Store(self.config.data_dir)
        try:
            store.save_feedback(body)
            try:
                report(store, self.config)
                self.queue_calendar_sync()
                return {"message": "Check-in saved; plan updated", "updated": True}
            except BusyError:
                return {"message": "Check-in saved; the plan will update after current processing", "updated": False}
        finally:
            store.close()

    def save_choice(self, name: str, body: dict):
        store = Store(self.config.data_dir)
        try:
            # Serialize read/merge/write so simultaneous tabs cannot lose choices
            # or measured results added by another request.
            store.db.execute("BEGIN IMMEDIATE")
            if name == "preferences":
                body = {**(store.setting(name, {}) or {}), **body}
                choice = validate_preferences(body, local_today())
            else:
                choice = validate_benchmark(body, local_today())
            if name == "benchmark" and choice is not None:
                history = store.setting("benchmark_history", [])
                history = [v for v in history if (v["date"], v["kind"]) != (choice["date"], choice["kind"])]
                history.append(choice)
                history.sort(key=lambda value: value["date"])
                store.set_settings({"benchmark": history[-1], "benchmark_history": history[-100:]})
            else:
                store.set_setting(name, choice)
            try:
                report(store, self.config)
                self.queue_calendar_sync()
                return {"message": "Training choices saved; plan updated", "updated": True}
            except BusyError:
                return {"message": "Training choices saved; plan update queued", "updated": False}
        finally:
            store.close()


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, config: Config, port: int, auth_factory=GarminAuth.from_environment):
        self.app = Application(config, auth_factory)
        super().__init__(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    server: LocalServer

    def log_message(self, *args):
        pass

    def respond(self, status, body, content_type="application/json", *, etag=None, download=None):
        payload = json.dumps(body, allow_nan=False).encode() if content_type == "application/json" else body.encode("utf-8")
        if etag and self.headers.get("If-None-Match") == etag:
            status, payload = 304, b""
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "private, no-cache" if etag else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if etag:
            self.send_header("ETag", etag)
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        try:
            if self.command != "HEAD":
                self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def local_host(self):
        port = self.server.server_port
        return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def do_GET(self):
        if not self.local_host():
            return self.respond(403, {"error": "Local access only"})
        path = urlsplit(self.path).path
        try:
            if path == "/":
                html = files("fivek").joinpath("dashboard.html").read_text(encoding="utf-8")
                return self.respond(200, html.replace("__CSRF_TOKEN__", self.server.app.token), "text/html")
            assets = {"/assets/dashboard.js": "text/javascript", "/assets/dashboard.css": "text/css"}
            if path in assets:
                resource = files("fivek").joinpath("assets", path.rsplit("/", 1)[1])
                return self.respond(200, resource.read_text(encoding="utf-8"), assets[path])
            if path == "/api/status":
                return self.respond(200, self.server.app.status())
            if path == "/api/calendar/status":
                from .calendar_sync import calendar_status
                return self.respond(200, calendar_status(self.server.app.config))
            if path == "/api/diagnostics":
                from .maintenance import diagnostics
                return self.respond(200, diagnostics(self.server.app.config))
            if path.startswith("/api/activities/"):
                activity_id = path.rsplit("/", 1)[1]
                if not activity_id.isascii() or not activity_id.isdigit() or len(activity_id) > 24:
                    return self.respond(400, {"error": "Invalid activity ID"})
                from .activity import activity_detail
                store = Store(self.server.app.config.data_dir)
                try:
                    with store.snapshot():
                        detail = activity_detail(store, activity_id)
                finally:
                    store.close()
                return self.respond(200, detail) if detail else self.respond(404, {"error": "Activity not found"})
            if path in {"/api/report", "/training-plan.ics", "/report.json", "/insights.md"}:
                result = self.server.app.get_report()
                if path.endswith(".ics"):
                    return self.respond(200, calendar(result["plan"]), "text/calendar", download="fivek-plan.ics")
                if path.endswith(".md"):
                    from .pipeline import markdown
                    return self.respond(200, markdown(result), "text/markdown", download="fivek-insights.md")
                if path == "/report.json":
                    return self.respond(200, json.dumps(result, indent=2), "application/octet-stream", download="fivek-report.json")
                etag = '"' + hashlib.sha256(json.dumps(result).encode()).hexdigest() + '"'
                return self.respond(200, result, etag=etag)
            return self.respond(404, {"error": "Resource not found"})
        except BusyError as exc:
            return self.respond(503, {"error": str(exc)})
        except Exception:
            return self.respond(500, {"error": "Local data could not be read. Saved data is retained; retry shortly."})

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        if not self.local_host() or not secrets.compare_digest(self.headers.get("X-FiveK-Token", ""), self.server.app.token):
            return self.respond(403, {"error": "Reload the local dashboard before submitting"})
        try:
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self.respond(415, {"error": "Use application/json"})
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192:
                raise ValueError("Invalid request size")
            def reject_constant(_):
                raise ValueError("Non-finite JSON number")
            body = json.loads(self.rfile.read(length), parse_constant=reject_constant)
            if not isinstance(body, dict):
                raise ValueError("Expected an object")
        except (ValueError, UnicodeDecodeError):
            return self.respond(400, {"error": "Invalid JSON request"})
        path = urlsplit(self.path).path
        try:
            if path == "/api/calendar/connect":
                from .calendar_sync import connect_apple, sync_calendar
                result = connect_apple(self.server.app.config, body)
                result["sync"] = sync_calendar(self.server.app.config, self.server.app.get_report())
                return self.respond(200, result)
            if path == "/api/calendar/sync":
                from .calendar_sync import sync_calendar
                return self.respond(200, sync_calendar(self.server.app.config, self.server.app.get_report()))
            if path == "/api/refresh":
                self.server.app.start_refresh(body)
                return self.respond(202, {"message": "Refresh started"})
            if path == "/api/cancel":
                self.server.app.cancel()
                return self.respond(202, {"message": "Cancellation requested; saved data is retained"})
            if path == "/api/backup":
                from .maintenance import create_backup
                backup = create_backup(self.server.app.config)
                return self.respond(201, {"message": "Local database backup created", "path": backup["path"], "source_records": backup["source_records"]})
            if path == "/api/feedback":
                result = self.server.app.save_feedback(body)
                return self.respond(200 if result["updated"] else 202, result)
            if path in {"/api/preferences", "/api/benchmark"}:
                result = self.server.app.save_choice(path.rsplit("/", 1)[1], body)
                return self.respond(200 if result["updated"] else 202, result)
            return self.respond(404, {"error": "Resource not found"})
        except BusyError as exc:
            return self.respond(409, {"error": str(exc)})
        except (ValueError, CalendarError) as exc:
            return self.respond(400, {"error": str(exc)})
        except Exception:
            return self.respond(500, {"error": "The local operation could not complete. Saved data is retained."})
