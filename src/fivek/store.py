from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        with file_lock(directory, "database-init.lock", "Database initialization is busy; retry shortly.", wait_seconds=5):
            self.db = sqlite3.connect(directory / "training.sqlite3", timeout=30)
            try:
                self._initialize()
            except BaseException:
                self.db.close()
                raise

    def _initialize(self):
        self.db.row_factory = sqlite3.Row
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 2:
            raise RuntimeError("This database was created by a newer FiveK version. Use that version to open it.")
        if self.db.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            self.db.execute("PRAGMA journal_mode=WAL")
        if version < 2:
            self.db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS raw (
                    stream TEXT NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL,
                    fetched_at TEXT NOT NULL, PRIMARY KEY(stream,key));
                CREATE TABLE IF NOT EXISTS fetches (
                    stream TEXT NOT NULL, key TEXT NOT NULL, status TEXT NOT NULL,
                    attempted_at TEXT NOT NULL, message TEXT, PRIMARY KEY(stream,key));
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
                    status TEXT NOT NULL, progress TEXT NOT NULL, message TEXT);
                CREATE TABLE IF NOT EXISTS feedback (
                    date TEXT PRIMARY KEY, fatigue INTEGER NOT NULL, soreness INTEGER NOT NULL,
                    pain INTEGER NOT NULL, football_minutes INTEGER, football_rpe INTEGER,
                    notes TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY, generated_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS raw_revisions (
                    id INTEGER PRIMARY KEY, stream TEXT NOT NULL, key TEXT NOT NULL,
                    fetched_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS revisions_resource ON raw_revisions(stream,key,id DESC);
                CREATE INDEX IF NOT EXISTS fetches_status ON fetches(status,attempted_at);
            """)
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(runs)")}
            if "telemetry" not in columns:
                self.db.execute("ALTER TABLE runs ADD COLUMN telemetry TEXT NOT NULL DEFAULT '{}'")
            self.db.execute("PRAGMA user_version=2")
            self.db.commit()


    def close(self):
        self.db.close()

    def put(self, stream: str, key: str, payload: Any) -> bool:
        serialized = json.dumps(payload, allow_nan=False, sort_keys=True)
        previous = self.db.execute("SELECT payload,fetched_at FROM raw WHERE stream=? AND key=?", (stream, key)).fetchone()
        changed = previous is None or (previous["payload"] != serialized and json.loads(previous["payload"]) != payload)
        if previous and changed:
            self.db.execute("INSERT INTO raw_revisions(stream,key,fetched_at,payload) VALUES (?,?,?,?)",
                            (stream, key, previous["fetched_at"], previous["payload"]))
            self.db.execute("DELETE FROM raw_revisions WHERE stream=? AND key=? AND id NOT IN "
                            "(SELECT id FROM raw_revisions WHERE stream=? AND key=? ORDER BY id DESC LIMIT 3)",
                            (stream, key, stream, key))
        self.db.execute("INSERT OR REPLACE INTO raw VALUES (?,?,?,?)",
                        (stream, key, serialized, now()))
        if changed:
            self.bump_revision()
        self.status(stream, key, "ok" if payload not in (None, [], {}) else "empty")
        return changed

    def status(self, stream: str, key: str, status: str, message: str | None = None):
        self.db.execute("INSERT OR REPLACE INTO fetches VALUES (?,?,?,?,?)",
                        (stream, key, status, now(), message))
        self.db.commit()

    def cached(self, stream: str, key: str) -> dict | None:
        row = self.db.execute("SELECT f.*,r.key IS NOT NULL has_payload FROM fetches f LEFT JOIN raw r ON r.stream=f.stream AND r.key=f.key WHERE f.stream=? AND f.key=?", (stream, key)).fetchone()
        return dict(row) if row else None

    def resource_coverage(self, expected: dict[str, list[str]]) -> dict:
        streams = []
        for stream, keys in expected.items():
            rows = self.db.execute("SELECT f.status,r.key IS NOT NULL has_payload,COUNT(*) n FROM fetches f "
                                   "JOIN json_each(?) e ON e.value=f.key LEFT JOIN raw r ON r.stream=f.stream AND r.key=f.key "
                                   "WHERE f.stream=? GROUP BY f.status,has_payload", (json.dumps(keys), stream))
            counts = {"ok": 0, "empty": 0, "error": 0, "unavailable": 0}
            for row in rows:
                if row["status"] in counts and (row["status"] not in {"ok", "empty"} or row["has_payload"]):
                    counts[row["status"]] += row["n"]
            streams.append({"stream": stream, "expected": len(keys), **counts,
                            "missing": max(0, len(keys) - sum(counts.values()))})
        return {"expected": sum(s["expected"] for s in streams),
                **{key: sum(s[key] for s in streams) for key in ("ok", "empty", "error", "unavailable", "missing")},
                "streams": streams,
                "note": "Counts cover the configured dates and latest discovered activity membership. Empty and unavailable are handled outcomes, not sensor readings; missing means an expected resource has no usable fetch status."}

    def records(self, stream: str) -> dict[str, Any]:
        return {r["key"]: json.loads(r["payload"]) for r in self.db.execute(
            "SELECT key,payload FROM raw WHERE stream=? ORDER BY key", (stream,))}

    def get(self, stream: str, key: str) -> Any:
        row = self.db.execute("SELECT payload FROM raw WHERE stream=? AND key=?", (stream, key)).fetchone()
        return json.loads(row[0]) if row else None

    def setting(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return row[0]  # Version 1 programme dates were stored as plain strings.

    def set_setting(self, key: str, value: Any):
        self.set_settings({key: value})

    def set_settings(self, values: dict[str, Any]):
        changed = False
        for key, value in values.items():
            if self.setting(key, object()) == value:
                continue
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value, allow_nan=False, sort_keys=True)))
            changed = changed or key in {"preferences", "benchmark", "benchmark_history", "session_overrides"}
        if changed:
            self.bump_revision()
        self.db.commit()

    def bump_revision(self):
        self.db.execute("INSERT INTO settings(key,value) VALUES ('data_revision','1') "
                        "ON CONFLICT(key) DO UPDATE SET value=CAST(CAST(value AS INTEGER)+1 AS TEXT)")

    @contextmanager
    def snapshot(self):
        """Read a coherent SQLite snapshot without holding a write transaction."""
        if self.db.in_transaction:
            raise RuntimeError("Finish the current transaction before starting a snapshot.")
        self.db.execute("BEGIN")
        try:
            yield
        finally:
            self.db.rollback()

    def start_run(self) -> int:
        # The exclusive refresh lock means unfinished entries belong to an interrupted process.
        self.db.execute("UPDATE runs SET status='interrupted',finished_at=? WHERE status='running'", (now(),))
        cur = self.db.execute("INSERT INTO runs(started_at,status,progress) VALUES (?,'running','Starting')", (now(),))
        self.db.commit()
        return cur.lastrowid

    def progress(self, run: int, message: str, telemetry: dict | None = None):
        if telemetry is None:
            self.db.execute("UPDATE runs SET progress=? WHERE id=?", (message, run))
        else:
            self.db.execute("UPDATE runs SET progress=?,telemetry=? WHERE id=?", (message, json.dumps(telemetry), run))
        self.db.commit()

    def finish(self, run: int, status: str, message: str):
        self.db.execute("UPDATE runs SET status=?,finished_at=?,message=?,progress=? WHERE id=?",
                        (status, now(), message, "Finished", run))
        self.db.commit()

    def sync_status(self) -> dict:
        row = self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        run = dict(row) if row else None
        if run:
            run["telemetry"] = json.loads(run.get("telemetry") or "{}")
        counts = {r["status"]: r["n"] for r in self.db.execute("SELECT status,COUNT(*) n FROM fetches GROUP BY status")}
        errors = [dict(r) for r in self.db.execute(
            "SELECT stream,key,status,message FROM fetches WHERE status IN ('error','unavailable') ORDER BY attempted_at DESC LIMIT 20")]
        coverage = [dict(r) for r in self.db.execute(
            "SELECT stream,COUNT(*) records,MIN(key) first_key,MAX(key) last_key FROM raw GROUP BY stream")]
        return {"run": run, "counts": counts, "issues": errors, "coverage": coverage}

    def save_report(self, report: dict):
        self.db.execute("INSERT INTO reports(generated_at,payload) VALUES (?,?)", (report["generated_at"], json.dumps(report, allow_nan=False)))
        self.db.execute("DELETE FROM reports WHERE id NOT IN (SELECT id FROM reports ORDER BY id DESC LIMIT 10)")
        self.db.commit()

    def latest_report(self) -> dict | None:
        row = self.db.execute("SELECT payload FROM reports ORDER BY id DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else None

    def feedback(self) -> dict[str, dict]:
        return {r["date"]: dict(r) for r in self.db.execute("SELECT * FROM feedback ORDER BY date")}

    def save_feedback(self, entry: dict):
        from datetime import date
        day = date.fromisoformat(entry["date"]).isoformat()
        values = []
        for key in ("fatigue", "soreness", "pain"):
            value = entry.get(key)
            if type(value) is not int or not 0 <= value <= 10:
                raise ValueError(f"{key} must be an integer from 0 to 10")
            values.append(value)
        minutes, rpe = entry.get("football_minutes"), entry.get("football_rpe")
        if minutes is not None and (type(minutes) is not int or not 0 <= minutes <= 240):
            raise ValueError("football_minutes must be an integer from 0 to 240")
        if rpe is not None and (type(rpe) is not int or not 1 <= rpe <= 10):
            raise ValueError("football_rpe must be an integer from 1 to 10")
        notes = entry.get("notes", "")
        if not isinstance(notes, str) or len(notes) > 2000:
            raise ValueError("notes must contain at most 2000 characters")
        self.db.execute("INSERT OR REPLACE INTO feedback VALUES (?,?,?,?,?,?,?)",
                        (day, *values, minutes, rpe, notes))
        self.bump_revision()
        self.db.commit()


class BusyError(RuntimeError):
    """A local operation is already being performed by another process."""


@contextmanager
def file_lock(directory: Path, name: str, message: str, *, wait_seconds: float = 0):
    """OS-owned lock is released automatically on crash; works across processes."""
    directory.mkdir(parents=True, exist_ok=True)
    handle = open(directory / name, "a+b")
    locked = False
    deadline = time.monotonic() + wait_seconds
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
            # Reading a byte owned by another Windows lock raises before the
            # retry loop. Initialize only after acquiring our own byte lock.
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
        locked = True
        yield
    except OSError as exc:
        if not locked:
            raise BusyError(message) from exc
        raise
    finally:
        if locked and os.name == "nt":
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


def refresh_lock(directory: Path):
    return file_lock(directory, "refresh.lock", "A Garmin refresh is already running.")


def report_lock(directory: Path):
    return file_lock(directory, "report.lock", "The training report is being updated.", wait_seconds=2)


def refresh_active(directory: Path) -> bool:
    try:
        with refresh_lock(directory):
            return False
    except RuntimeError:
        return True
