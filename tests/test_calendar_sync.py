"""Offline calendar ownership, convergence and automatic refresh checks."""
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from fivek.calendar_sync import CalendarError, sync_calendar, desired_sessions
from fivek.automation import run_cycle
from fivek.config import Config
from fivek.store import Store, refresh_lock
from fivek.webapp import Application
from fivek.config import local_today
from garmin_api import GarminMFARequired
from test_pipeline import Provider as GarminProvider


TODAY = date(2026, 10, 9)


class CalendarProvider:
    def __init__(self):
        self.events = {}
        self.writes = 0

    def get(self, uid):
        return self.events.get(uid)

    def put(self, uid, data, existing=None):
        self.events[uid] = SimpleNamespace(data=data)
        self.writes += 1

    def delete(self, existing):
        self.events = {uid: value for uid, value in self.events.items() if value is not existing}
        self.writes += 1


def session(day, **values):
    return {"date": day.isoformat(), "kind": "easy", "title": "Easy run", "status": "planned",
            "effort": "easy", "km": 3, "details": "Conversational running", **values}


class CalendarChecks(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.config = Config(data_dir=Path(self.directory.name), start=TODAY-timedelta(days=2), request_delay=0)
        self.provider = CalendarProvider()
        self.result = {"as_of": TODAY.isoformat(), "generated_at": "2026-10-09T07:00:00+00:00",
                       "metrics": {"activities": []}, "plan": {"sessions": [session(TODAY), session(TODAY+timedelta(days=2))]}}

    def tearDown(self):
        self.directory.cleanup()

    def test_rolling_window_and_repeated_refresh_create_no_duplicates(self):
        self.result["plan"]["sessions"].extend([session(TODAY+timedelta(days=7)), session(TODAY+timedelta(days=1), kind="football")])
        first = sync_calendar(self.config, self.result, self.provider)
        self.assertEqual(first["created"], 2)
        self.result["generated_at"] = "2026-10-09T09:00:00+00:00"
        second = sync_calendar(self.config, self.result, self.provider)
        self.assertEqual(second["unchanged"], 2)
        self.assertEqual(self.provider.writes, 2)
        self.assertTrue(all("TRANSP:TRANSPARENT" in event.data for event in self.provider.events.values()))

    def test_changed_or_paused_prescriptions_update_or_remove_owned_events(self):
        sync_calendar(self.config, self.result, self.provider)
        self.result["plan"]["sessions"][1]["km"] = 2
        self.assertEqual(sync_calendar(self.config, self.result, self.provider)["updated"], 1)
        self.result["plan"]["sessions"][1]["kind"] = "rest"
        self.assertEqual(sync_calendar(self.config, self.result, self.provider)["deleted"], 1)

    def test_unrecorded_history_and_late_watch_sync_converge(self):
        sync_calendar(self.config, self.result, self.provider)
        self.result["as_of"] = (TODAY+timedelta(days=1)).isoformat()
        self.result["plan"]["sessions"] = [self.result["plan"]["sessions"][1]]
        self.assertEqual(sync_calendar(self.config, self.result, self.provider)["updated"], 1)
        self.assertTrue(any("No run recorded" in event.data for event in self.provider.events.values()))
        self.assertEqual(sync_calendar(self.config, self.result, self.provider)["unchanged"], 2)
        self.result["metrics"]["activities"] = [{"kind": "run", "date": TODAY.isoformat()}]
        self.assertEqual(sync_calendar(self.config, self.result, self.provider)["updated"], 1)
        self.assertFalse(any("No run recorded" in event.data for event in self.provider.events.values()))

    def test_ownership_mismatch_is_not_overwritten(self):
        sync_calendar(self.config, self.result, self.provider)
        event = next(iter(self.provider.events.values()))
        event.data = event.data.replace("X-FIVEK-MANAGED:TRUE", "X-FIVEK-MANAGED:FALSE")
        with self.assertRaises(CalendarError):
            sync_calendar(self.config, self.result, self.provider)
        self.assertEqual(self.provider.writes, 2)

    def test_recorded_run_is_retained_even_if_pain_changes_prescription_to_rest(self):
        self.result["plan"]["sessions"] = [session(TODAY, kind="rest", status="recorded", recorded_activity_ids=["1"], actual_run_km=3, next_action="Pause impact because of pain")]
        selected = desired_sessions(self.result)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["title"], "Run recorded · Recover")
        self.assertIn("pain", selected[0]["details"])

    def test_manual_calendar_deletion_does_not_recreate_past_history(self):
        sync_calendar(self.config, self.result, self.provider)
        self.provider.events.clear()
        self.result["as_of"] = (TODAY+timedelta(days=3)).isoformat()
        self.result["plan"]["sessions"] = []
        self.assertEqual(sync_calendar(self.config, self.result, self.provider)["created"], 0)

    def test_cycle_refreshes_plan_and_calendar_and_repeats_without_writes(self):
        auth = lambda: SimpleNamespace(login=lambda: GarminProvider())
        with patch("fivek.pipeline.local_today", return_value=TODAY), patch("fivek.sync.local_today", return_value=TODAY):
            first = run_cycle(self.config, auth, self.provider)
            second = run_cycle(self.config, auth, self.provider)
        self.assertEqual(first["status"], "complete")
        self.assertEqual(second["status"], "complete")
        self.assertEqual(second["calendar"]["created"], 0)
        self.assertEqual(second["calendar"]["updated"], 0)
        self.assertGreater(second["calendar"]["unchanged"], 0)

    def test_verification_required_and_concurrent_refresh_are_visible(self):
        def login():
            raise GarminMFARequired()
        with patch("fivek.pipeline.local_today", return_value=TODAY), patch("fivek.sync.local_today", return_value=TODAY):
            value = run_cycle(self.config, lambda: SimpleNamespace(login=login), self.provider)
        self.assertEqual(value["status"], "needs_auth")
        with refresh_lock(self.config.data_dir):
            self.assertEqual(run_cycle(self.config, lambda: SimpleNamespace(login=login), self.provider)["status"], "deferred")

    def test_dashboard_saves_queue_calendar_updates_without_network_or_real_credentials(self):
        (self.config.data_dir / "calendar-credentials.bin").write_bytes(b"isolated-presence-marker")
        app = Application(self.config)
        captured = []
        with patch("fivek.calendar_sync.sync_calendar", side_effect=lambda config, result: captured.append(result)):
            value = app.save_feedback({"date": local_today().isoformat(), "fatigue": 2, "soreness": 1, "pain": 0})
            self.assertTrue(value["updated"])
            thread = app.calendar_thread
            if thread is not None:
                thread.join(timeout=5)
            self.assertTrue(captured)
            self.assertEqual(captured[-1]["metrics"]["feedback"][-1]["fatigue"], 2)
            app.save_choice("preferences", {"gym": False})
            thread = app.calendar_thread
            if thread is not None:
                thread.join(timeout=5)
            self.assertFalse(captured[-1]["metrics"]["preferences"]["gym"])


if __name__ == "__main__":
    unittest.main()
