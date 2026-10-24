"""Offline checks for checkpoint recovery, processing and calendar prescriptions."""
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from garmin_api import GarminConnectionError
from fivek.analysis import analyse
from fivek.config import Config
from fivek.pipeline import calendar, report
from fivek.plan import make_plan
from fivek.store import Store, refresh_lock
from fivek.sync import DAILY, Sync


TODAY = date(2026, 10, 3)


class Provider:
    def __init__(self):
        self.calls = []
        self.limited = None
        self.items = [{"activityId": 1, "activityName": "Easy run", "startTimeLocal": "2026-08-30 10:00:00",
                       "activityType": {"typeKey": "running"}, "distance": 5000, "duration": 1800, "elapsedDuration": 1810}]

    def fetch(self, method, *args):
        self.calls.append((method, args))
        if self.limited == (method, args):
            raise GarminConnectionError("Garmin temporarily limited API requests.", status_code=429)
        if method == "get_user_profile":
            return {"id": "fixture-account"}
        if method == "get_activities":
            return self.items if args[0] == 0 else []
        if method == "get_stats":
            return {"restingHeartRate": 52, "totalSteps": 7000}
        if method == "get_sleep_data":
            return {"dailySleepDTO": {"sleepTimeSeconds": 8 * 3600}}
        if method == "get_hrv_data":
            return {"hrvSummary": {"status": "BALANCED", "lastNightAvg": 65}}
        if method == "get_training_readiness":
            return [{"inputContext": "AFTER_WAKEUP_RESET", "score": 70}]
        if method == "get_spo2_data":
            raise GarminConnectionError("Metric unavailable.", status_code=404)
        return {}


class PipelineChecks(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.config = Config(data_dir=Path(self.directory.name), start=TODAY - timedelta(days=34), request_delay=0)
        self.store = Store(self.config.data_dir)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def sync(self, provider):
        run = self.store.start_run()
        result = Sync(provider, self.store, self.config, lambda _: None).run(run, today=TODAY)
        self.store.finish(run, "complete", "Completed offline fixture")
        return result

    def test_full_onboarding_crosses_recent_window_and_caches_history(self):
        provider = Provider()
        result = self.sync(provider)
        self.assertEqual(result["activities"], 1)
        self.assertEqual(len(self.store.records("daily")), 35)
        self.assertEqual(len(self.store.records("activity")), 1)
        self.assertEqual(self.store.cached("spo2", TODAY.isoformat())["status"], "unavailable")
        provider.calls.clear()
        self.sync(provider)
        stats_calls = [args for method, args in provider.calls if method == "get_stats"]
        self.assertEqual(len(stats_calls), 8)  # Inclusive current day + previous seven.
        self.assertFalse(any(method == "get_activity" for method, _ in provider.calls))
        generated = report(self.store, self.config, TODAY)
        self.assertEqual(generated["metrics"]["benchmark"]["seconds"], 1810)
        self.assertEqual(generated["metrics"]["sleep_average_hours"], 8)
        self.assertTrue((self.config.data_dir / "report.json").exists())
        self.assertTrue(calendar(generated["plan"]).endswith("END:VCALENDAR\r\n"))
        self.assertEqual((self.config.data_dir / "training-plan.ics").read_bytes(), calendar(generated["plan"]).encode("utf-8"))

    def test_rate_limit_preserves_saved_days_and_resumes(self):
        provider = Provider()
        provider.limited = ("get_stats", ((TODAY - timedelta(days=1)).isoformat(),))
        run = self.store.start_run()
        with self.assertRaises(GarminConnectionError):
            Sync(provider, self.store, self.config, lambda _: None).run(run, today=TODAY)
        self.assertIn(TODAY.isoformat(), self.store.records("daily"))
        self.assertEqual(self.store.cached("daily", (TODAY - timedelta(days=1)).isoformat())["status"], "error")
        provider.limited = None
        self.sync(provider)
        self.assertEqual(len(self.store.records("daily")), 35)
        self.assertFalse(any(v["status"] == "error" for v in self.store.sync_status()["issues"]))

    def test_missing_wellness_is_unknown_and_fresh_pain_pauses_football(self):
        self.store.put("daily", TODAY.isoformat(), {})
        self.store.put("readiness", TODAY.isoformat(), [])
        self.assertEqual(analyse(self.store, TODAY)["recovery"]["status"], "unknown")
        self.store.save_feedback({"date": TODAY.isoformat(), "fatigue": 0, "soreness": 0, "pain": 2})
        generated = report(self.store, self.config, TODAY)
        first_three = generated["plan"]["sessions"][:3]
        self.assertTrue(all(s["kind"] == "rest" for s in first_three))
        self.assertEqual(generated["metrics"]["recovery"]["status"], "pause")

    def test_quality_phase_does_not_reset_on_every_refresh(self):
        planning_day = date(2026, 10, 7)  # Wednesday: upcoming Thursday is in the current week.
        self.store.save_feedback({"date": planning_day.isoformat(), "fatigue": 0, "soreness": 0, "pain": 0})
        metrics = analyse(self.store, planning_day)
        metrics.update(baseline_weekly_km=20, recent_run_count=14, recent_running_days=14,
                       program_start=(planning_day - timedelta(days=28)).isoformat())
        plan = make_plan(metrics, self.config, planning_day)
        quality = [s for s in plan["sessions"][:7] if s["kind"] == "quality"]
        self.assertEqual(len(quality), 1)
        self.assertEqual(quality[0]["weekday"], "Thursday")
        self.assertTrue(all(s["kind"] != "quality" for s in plan["sessions"] if (self.config.target - date.fromisoformat(s["date"])).days <= 10))

    def test_removed_activity_leaves_audit_data_and_excludes_it_from_insights(self):
        provider = Provider()
        self.sync(provider)
        provider.items = []
        self.sync(provider)
        self.assertIn("1", self.store.records("activities"))
        self.assertEqual(analyse(self.store, TODAY)["activities"], [])

    def test_refresh_lock_excludes_second_writer(self):
        with refresh_lock(self.config.data_dir):
            with self.assertRaises(RuntimeError):
                with refresh_lock(self.config.data_dir):
                    pass

    def test_goal_effort_and_taper_summaries_match_prescriptions(self):
        metrics = analyse(self.store, TODAY)
        plan = make_plan(metrics, self.config, TODAY)
        self.assertEqual(plan["sessions"][-1]["date"], "2027-01-01")
        self.assertEqual(plan["sessions"][-1]["kind"], "race")
        self.assertEqual(plan["sessions"][-2]["kind"], "rest")
        late_football = [s for s in plan["sessions"] if s["kind"] == "football" and s["date"] >= "2026-12-27"]
        self.assertTrue(all(s["optional"] for s in late_football))
        for week in plan["weeks"]:
            start = date.fromisoformat(week["start"])
            rows = [s for s in plan["sessions"] if start <= date.fromisoformat(s["date"]) <= start + timedelta(days=6)
                    and s["kind"] in ("easy", "quality", "race")]
            self.assertAlmostEqual(week["planned_run_km"], sum(s["km"] or 0 for s in rows), places=1)
            self.assertEqual(week["planned_run_minutes"], sum(s["minutes"] or 0 for s in rows))

    def test_stale_fatigue_expires_but_unresolved_pain_needs_an_update(self):
        old_date = (TODAY - timedelta(days=10)).isoformat()
        self.store.save_feedback({"date": old_date, "fatigue": 10, "soreness": 10, "pain": 0})
        metrics = analyse(self.store, TODAY)
        self.assertEqual(metrics["recovery"]["status"], "unknown")
        self.assertNotEqual(make_plan(metrics, self.config, TODAY)["sessions"][0]["kind"], "rest")
        self.store.save_feedback({"date": old_date, "fatigue": 10, "soreness": 10, "pain": 4})
        self.assertEqual(analyse(self.store, TODAY)["recovery"]["status"], "pause")
        self.store.save_feedback({"date": TODAY.isoformat(), "fatigue": 0, "soreness": 0, "pain": 0})
        self.assertEqual(analyse(self.store, TODAY)["recovery"]["status"], "normal")

    def test_consistent_actual_running_can_advance_budget_with_a_cap(self):
        metrics = analyse(self.store, TODAY)
        metrics.update(baseline_weekly_km=10, recent_run_count=10)
        metrics["recovery"]["status"] = "normal"
        metrics["weeks"][-1].update(run_km=20, run_count=3, running_days=3)
        metrics["weeks"][-2].update(run_km=10, run_count=3, running_days=3)
        plan = make_plan(metrics, self.config, TODAY)
        self.assertEqual(plan["weeks"][0]["weekly_run_budget_km"], 12.5)
        metrics["weeks"][-1]["running_days"] = 1
        self.assertEqual(make_plan(metrics, self.config, TODAY)["weeks"][0]["weekly_run_budget_km"], 10.4)

    def test_old_empty_data_stays_cached_until_explicit_full_refresh(self):
        day = (TODAY - timedelta(days=30)).isoformat()
        self.store.put("training", day, {})
        self.store.db.execute("UPDATE fetches SET attempted_at='2024-01-01T00:00:00+00:00' WHERE stream='training'")
        self.store.db.commit()
        provider = Provider()
        sync = Sync(provider, self.store, self.config, lambda _: None)
        sync.fetch("training", day, "get_training_status", day)
        self.assertEqual(provider.calls, [])
        sync.fetch("training", day, "get_training_status", day, force=True)
        self.assertEqual(len(provider.calls), 1)


if __name__ == "__main__":
    unittest.main()
