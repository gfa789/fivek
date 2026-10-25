from __future__ import annotations

import time
import json
from datetime import date, datetime, timedelta
from typing import Callable

from garmin_api import GarminAPI, GarminAuthenticationError, GarminConnectionError
from .config import Config, local_today
from .store import Store, BusyError
from .resources import DAILY, ACTIVITY, ACCOUNT, RANGED, expected_resources, validate_payload


class SyncCancelled(RuntimeError):
    """An import was stopped by the owner; committed checkpoints are retained."""

    def __init__(self):
        super().__init__("Garmin refresh cancelled. Saved progress will resume next time.")


class Sync:
    def __init__(self, api: GarminAPI, store: Store, config: Config,
                 progress: Callable[[str], None] = print, publish: Callable[[], None] | None = None,
                 cancel=None):
        self.api, self.store, self.config, self.progress = api, store, config, progress
        self.publish = publish or (lambda: None)
        self.errors = 0
        self.unavailable = 0
        self._last_call = 0.0
        self.cancel = cancel
        self._run_id = None
        self._started = time.monotonic()
        self.account_verified = False
        self._last_published = None
        self._unavailable_adapters = set()
        self._cancel_marker_stamp = None
        self._cancel_marker_run = None
        self.stats = {"requests": 0, "responses": 0, "cache_hits": 0, "errors": 0,
                      "unavailable": 0, "phase": "starting", "completed": 0, "total": None}

    def check_cancel(self):
        requested = self.cancel is not None and self.cancel.is_set()
        marker = self.config.data_dir / "cancel.request"
        if not requested and self._run_id is not None:
            try:
                info = marker.stat()
                stamp = (info.st_mtime_ns, info.st_size)
                if stamp != self._cancel_marker_stamp:
                    self._cancel_marker_run = json.loads(marker.read_text(encoding="utf-8")).get("run_id")
                    self._cancel_marker_stamp = stamp
                requested = self._cancel_marker_run == self._run_id
            except FileNotFoundError:
                self._cancel_marker_stamp = self._cancel_marker_run = None
            except (OSError, ValueError, AttributeError):
                pass
        if requested:
            raise SyncCancelled()

    def wait(self, seconds: float):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check_cancel()
            interval = min(.1, max(0, deadline - time.monotonic()))
            if self.cancel is not None:
                self.cancel.wait(interval)
            else:
                time.sleep(interval)
        self.check_cancel()

    def publish_report(self):
        fingerprint = (self.store.setting("data_revision", 0), self.errors, self.unavailable)
        if fingerprint == self._last_published:
            return
        try:
            self.publish()
            self._last_published = fingerprint
        except BusyError:
            pass  # A concurrent check-in is publishing; the next checkpoint publishes again.

    def telemetry(self) -> dict:
        return {**self.stats, "elapsed_seconds": round(time.monotonic() - self._started, 1)}

    def verify_account(self, payload):
        previous = self.store.get("profile", "account")
        previous_id = previous.get("id") if isinstance(previous, dict) else None
        expected = self.store.setting("garmin_account_id", str(previous_id) if previous_id is not None else None)
        received = payload.get("id") if isinstance(payload, dict) else None
        if type(received) not in (int, str) or not str(received).strip() or len(str(received)) > 128:
            raise GarminAuthenticationError("Garmin account identity could not be verified. Retry before importing more data.")
        if expected is not None and (received is None or str(received) != expected):
            raise GarminAuthenticationError("This data folder belongs to a different Garmin account, or account identity could not be verified. Use a separate FIVEK_DATA_DIR for another account.")
        if received is not None:
            self.store.set_setting("garmin_account_id", str(received))
            self.account_verified = True

    def call(self, method: str, *args):
        for attempt in range(3):
            self.check_cancel()
            self.wait(max(0, self.config.request_delay - (time.monotonic() - self._last_call)))
            self._last_call = time.monotonic()
            try:
                self.stats["requests"] += 1
                result = self.api.fetch(method, *args)
                self.stats["responses"] += 1
                return result
            except GarminConnectionError as exc:
                if exc.status_code in (403, 404, 501):
                    raise
                if exc.status_code == 429:
                    # Stop immediately: persist checkpoints and let the next refresh resume.
                    raise
                if attempt == 2:
                    raise
                self.wait(2 ** attempt)

    def fetch(self, stream: str, key: str, method: str, *args, force=False):
        self.check_cancel()
        cached = self.store.cached(stream, key)
        if cached and not force and (cached["status"] == "unavailable" or (cached["status"] in ("ok", "empty") and cached["has_payload"])):
            self.stats["cache_hits"] += 1
            return
        if method in self._unavailable_adapters:
            self.store.status(stream, key, "unavailable", "This Garmin adapter is unavailable.")
            self.unavailable += 1
            self.stats["unavailable"] = self.unavailable
            return
        try:
            payload = self.call(method, *args)
            if stream == "profile":
                self.verify_account(payload)
            validate_payload(stream, payload)
            self.store.put(stream, key, payload)
        except GarminAuthenticationError:
            raise
        except GarminConnectionError as exc:
            status = "unavailable" if exc.status_code in (403, 404, 501) else "error"
            self.store.status(stream, key, status, str(exc))
            if exc.status_code == 501:
                self._unavailable_adapters.add(method)
            if status == "error":
                self.errors += 1
                self.stats["errors"] = self.errors
                if exc.status_code == 429:
                    raise
                if self.errors >= 10:
                    raise GarminConnectionError("Repeated Garmin failures. Saved progress; retry refresh later.") from exc
            else:
                self.unavailable += 1
                self.stats["unavailable"] = self.unavailable

    def due(self, stream: str, key: str, hours: int) -> bool:
        """Revisit mutable recent observations on a clock, not on page views."""
        cached = self.store.cached(stream, key)
        if not cached or cached["status"] == "error":
            return True
        if cached["status"] == "unavailable":
            hours = max(hours, 24)  # Unsupported recent channels are retried at most daily.
        elif not cached["has_payload"]:
            return True
        age = datetime.now().astimezone() - datetime.fromisoformat(cached["attempted_at"])
        return age.total_seconds() >= hours * 3600

    def run(self, run_id: int, *, full=False, today: date | None = None):
        today = today or local_today()
        self._run_id = run_id

        def update(message):
            self.check_cancel()
            self.store.progress(run_id, message, self.telemetry())
            self.progress(message)

        for stream, method in ACCOUNT.items():
            self.fetch(stream, "account", method, force=full or stream == "profile" or self.due(stream, "account", 24))
            if stream == "profile" and not self.account_verified:
                raise GarminConnectionError("Garmin account identity could not be verified. Retry before importing more data.")
        # Two bounded reads per periodic cycle; preserve observations beyond
        # the rolling range so opening the dashboard never contacts Garmin.
        from .physiology import scale_readings, race_readings
        for stream, method in RANGED.items():
            initial = self.store.cached(stream, "recent") is None
            args = [(today - timedelta(days=89 if initial or full else 6)).isoformat(), today.isoformat()]
            if stream == "race_predictions":
                args.append("daily")
            self.fetch(stream, "recent", method, *args, force=full or self.due(stream, "recent", 2))
        for row in scale_readings(self.store.get("body_composition", "recent"), today):
            self.store.put("body_measurements", row["date"], row)
        for row in race_readings(self.store.get("race_predictions", "recent"), today):
            self.store.put("race_estimates", row["date"], row)
        seen: set[str] = set()
        changed: set[str] = set()
        oldest = today
        previous_roster = self.store.get("activity_roster", "current")
        previous_items = self.store.records("activities")
        incremental = previous_roster is not None and not full
        complete_scan = True
        cutoff = (today - timedelta(days=7)).isoformat()
        self.stats.update(phase="activities", completed=0, total=None)
        for page in range(2000):
            update(f"Discovering activities: page {page + 1}")
            items = self.call("get_activities", page * 100, 100)
            if not isinstance(items, list):
                raise GarminConnectionError("Garmin returned an unexpected activity list; refresh stopped safely.")
            if not items:
                break
            new = 0
            for item in items:
                if not isinstance(item, dict) or type(item.get("activityId")) not in (int, str) or not str(item["activityId"]).isascii() or not str(item["activityId"]).isdigit() or len(str(item["activityId"])) > 24 or int(item["activityId"]) <= 0:
                    raise GarminConnectionError("Garmin returned an invalid activity record.")
                key = str(item["activityId"])
                if key in seen:
                    continue
                seen.add(key)
                new += 1
                if self.store.put("activities", key, item):
                    changed.add(key)
                day = str(item.get("startTimeLocal") or item.get("startTimeGMT") or "")[:10]
                try:
                    oldest = min(oldest, date.fromisoformat(day))
                except ValueError:
                    pass
            self.stats["completed"] = len(seen)
            if not new:
                raise GarminConnectionError("Garmin repeated an activity page. Saved progress; refresh stopped.")
            # Garmin returns newest first. Cross the overlap window and stop at a
            # wholly known page; retain all older membership from the saved scan.
            if incremental and all(str(item["activityId"]) in previous_items and
                                   str(item.get("startTimeLocal") or item.get("startTimeGMT") or "")[:10] < cutoff
                                   for item in items):
                complete_scan = False
                break
        else:
            raise GarminConnectionError("Activity pagination limit reached. History is incomplete.")
        if not complete_scan:
            # Reconcile deletions only in the recent window we actually scanned.
            retained = {key for key in previous_roster if
                        str(previous_items.get(key, {}).get("startTimeLocal") or previous_items.get(key, {}).get("startTimeGMT") or "")[:10] < cutoff}
            seen |= retained
        # Only a complete scan (initial import or explicit administrator --full)
        # can remove older membership. Raw records remain available for recovery.
        self.store.put("activity_roster", "current", sorted(seen))
        oldest = min([today] + [date.fromisoformat(day) for key, item in self.store.records("activities").items()
                               if key in seen and (day := str(item.get("startTimeLocal") or item.get("startTimeGMT") or "")[:10])
                               and self.valid_day(day)])
        self.publish_report()
        # Current wellness is processed first so a long initial import is useful immediately.
        start = self.config.start or (oldest if seen else today - timedelta(days=365))
        if start > today:
            raise ValueError("History start must not be after today")
        day = today
        self.stats.update(phase="recent_recovery", completed=0, total=min(28, (today - start).days + 1))
        while day >= max(start, today - timedelta(days=27)):
            update(f"Fetching daily training and recovery: {day.isoformat()}")
            for stream, method in DAILY.items():
                self.fetch(stream, day.isoformat(), method, day.isoformat(),
                           force=full or ((today - day).days <= 7 and self.due(stream, day.isoformat(), 2 if (today - day).days <= 1 else 24)))
            if (today - day).days % 7 == 0:
                self.publish_report()
            self.stats["completed"] += 1
            day -= timedelta(days=1)
        self.publish_report()
        activity_items = sorted(self.store.records("activities").items(),
                                key=lambda pair: str(pair[1].get("startTimeLocal") or pair[1].get("startTimeGMT") or ""), reverse=True)
        self.stats.update(phase="activity_details", completed=0, total=len(seen))
        enriched = 0
        for index, (key, item) in enumerate(activity_items):
            if key not in seen:
                continue
            update(f"Enriching activity {index + 1}/{len(seen)}")
            activity_day = str(item.get("startTimeLocal") or item.get("startTimeGMT") or "")[:10]
            missing = any((cached := self.store.cached(stream, key)) is None or cached["status"] == "error" for stream in ACTIVITY)
            if not full and activity_day < cutoff and enriched >= 20 and (missing or key in changed):
                continue  # Older missing detail is filled in bounded background batches.
            for stream, method in ACTIVITY.items():
                self.fetch(stream, key, method, key, force=full or key in changed)
            if missing or key in changed:
                enriched += 1
            self.stats["completed"] += 1
        self.publish_report()
        self.stats.update(phase="historical_recovery", completed=0, total=max(0, (day - start).days + 1))
        backfilled = 0
        while day >= start:
            missing = any(self.store.cached(stream, day.isoformat()) is None or
                          self.store.cached(stream, day.isoformat())["status"] == "error" for stream in DAILY)
            if missing and backfilled >= 14 and not full:
                break
            update(f"Fetching daily training and recovery: {day.isoformat()}")
            for stream, method in DAILY.items():
                self.fetch(stream, day.isoformat(), method, day.isoformat(),
                           force=full)
            if missing:
                backfilled += 1
            if (today - day).days % 28 == 0:
                self.publish_report()
            self.stats["completed"] += 1
            day -= timedelta(days=1)
        self.stats.update(phase="complete", completed=self.stats["total"])
        self.store.progress(run_id, "Processing final insights", self.telemetry())
        return {"errors": self.errors, "unavailable": self.unavailable,
                "history_start": start.isoformat(), "history_end": today.isoformat(), "activities": len(seen),
                "coverage": self.store.resource_coverage(expected_resources(start, today, seen)),
                "telemetry": self.telemetry()}

    @staticmethod
    def valid_day(value):
        try:
            date.fromisoformat(value)
            return True
        except ValueError:
            return False
