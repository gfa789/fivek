"""Local diagnostics and coherent SQLite backups without authentication material."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone

from .config import Config
from .pipeline import atomic_text
from .store import Store, file_lock, now, refresh_active


def diagnostics(config: Config, *, check_database=False) -> dict:
    store = Store(config.data_dir)
    try:
        status = store.sync_status()
        result = {"data_directory": str(config.data_dir.resolve()),
                  "database_bytes": (config.data_dir / "training.sqlite3").stat().st_size,
                  "schema_version": store.db.execute("PRAGMA user_version").fetchone()[0],
                  "source_revision": store.setting("data_revision", 0),
                  "refresh_running": refresh_active(config.data_dir),
                  "last_refresh": status["run"], "fetch_status_counts": status["counts"],
                  "coverage": status["coverage"], "issues": status["issues"],
                  "has_report": store.latest_report() is not None}
        if check_database:
            result["database_check"] = "ok" if store.db.execute("PRAGMA quick_check").fetchone()[0] == "ok" else "Database check failed; preserve the file and restore a known-good backup."
        return result
    finally:
        store.close()


def create_backup(config: Config) -> dict:
    with file_lock(config.data_dir, "backup.lock", "A local database backup is already running."):
        return _create_backup(config)


def _create_backup(config: Config) -> dict:
    directory = config.data_dir / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    source_path = config.data_dir / "training.sqlite3"
    store = Store(config.data_dir)
    temporary = directory / ("pending-" + uuid.uuid4().hex + ".sqlite3")
    backup = None
    try:
        if shutil.disk_usage(directory).free < source_path.stat().st_size * 1.2:
            raise RuntimeError("Not enough free disk space to create a database backup.")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        destination = directory / (stamp + "-training.sqlite3")
        backup = sqlite3.connect(temporary)
        store.db.backup(backup, pages=256, sleep=.01)
        if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("Backup validation failed; the source database is retained.")
        version = backup.execute("PRAGMA user_version").fetchone()[0]
        records = backup.execute("SELECT COUNT(*) FROM raw").fetchone()[0]
        revision = backup.execute("SELECT value FROM settings WHERE key='data_revision'").fetchone()
        backup.close()
        backup = None
        os.replace(temporary, destination)
        digest = hashlib.sha256()
        with destination.open("rb") as content:
            for block in iter(lambda: content.read(1024 * 1024), b""):
                digest.update(block)
        manifest = {"created_at": now(), "database": destination.name,
                    "bytes": destination.stat().st_size, "sha256": digest.hexdigest(),
                    "schema_version": version, "source_records": records,
                    "source_revision": json.loads(revision[0]) if revision else 0,
                    "contents": "Garmin source data, check-ins, choices, prescriptions and reports. Authentication tokens and environment secrets are excluded.",
                    "restore": "Stop the dashboard and refresh process. Copy this database to a new FIVEK_DATA_DIR as training.sqlite3, then run fivek report with that directory."}
        atomic_text(destination.with_suffix(".json"), json.dumps(manifest, indent=2))
        return {**manifest, "path": str(destination.resolve())}
    finally:
        if backup is not None:
            backup.close()
        temporary.unlink(missing_ok=True)
        store.close()


def request_cancel(config: Config) -> bool:
    if not refresh_active(config.data_dir):
        return False
    store = Store(config.data_dir)
    try:
        run = store.sync_status()["run"]
        if not run or run["status"] != "running":
            return False
        atomic_text(config.data_dir / "cancel.request", json.dumps({"run_id": run["id"]}))
        return True
    finally:
        store.close()
