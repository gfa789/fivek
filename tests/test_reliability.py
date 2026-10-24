from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import threading
import unittest

from garmin_api import GarminAuthenticationError
from fivek.config import Config
from fivek.store import Store, report_lock
from fivek.sync import Sync, SyncCancelled
from test_pipeline import Provider, TODAY


class ReliabilityChecks(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.config = Config(data_dir=Path(self.temp.name), start=TODAY, request_delay=0)
        self.store = Store(self.config.data_dir)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_changed_responses_keep_three_previous_versions(self):
        for value in range(6):
            self.store.put("daily", TODAY.isoformat(), {"value": value})
        rows = self.store.db.execute("SELECT payload FROM raw_revisions ORDER BY id").fetchall()
        self.assertEqual([json.loads(r[0])["value"] for r in rows], [2, 3, 4])
        self.assertFalse(self.store.put("daily", TODAY.isoformat(), {"value": 5}))
        self.assertEqual(self.store.setting("data_revision"), 6)

    def test_snapshot_remains_coherent_during_another_connection_write(self):
        self.store.put("daily", TODAY.isoformat(), {"value": 1})
        with self.store.snapshot():
            self.assertEqual(self.store.get("daily", TODAY.isoformat())["value"], 1)
            other = Store(self.config.data_dir)
            try:
                other.put("daily", TODAY.isoformat(), {"value": 2})
                self.assertEqual(self.store.get("daily", TODAY.isoformat())["value"], 1)
            finally:
                other.close()
        self.assertEqual(self.store.get("daily", TODAY.isoformat())["value"], 2)

    def test_cancel_does_not_remove_committed_data(self):
        self.store.put("daily", TODAY.isoformat(), {"value": 1})
        cancel = threading.Event()
        cancel.set()
        provider = Provider()
        with self.assertRaises(SyncCancelled):
            Sync(provider, self.store, self.config, lambda _: None, cancel=cancel).run(self.store.start_run(), today=TODAY)
        self.assertEqual(provider.calls, [])
        self.assertEqual(self.store.get("daily", TODAY.isoformat())["value"], 1)

    def test_stale_cancel_marker_does_not_cancel_a_new_run(self):
        marker = self.config.data_dir / "cancel.request"
        marker.write_text(json.dumps({"run_id": -1}))
        result = Sync(Provider(), self.store, self.config, lambda _: None).run(self.store.start_run(), today=TODAY)
        self.assertEqual(result["activities"], 1)
        self.assertGreater(result["telemetry"]["requests"], 0)

    def test_account_mismatch_rejects_before_activity_discovery(self):
        self.store.put("profile", "account", {"id": 111})
        class OtherAccount(Provider):
            def fetch(self, method, *args):
                return {"id": 222} if method == "get_user_profile" else super().fetch(method, *args)
        provider = OtherAccount()
        with self.assertRaises(GarminAuthenticationError):
            Sync(provider, self.store, self.config, lambda _: None).run(self.store.start_run(), today=TODAY)
        self.assertEqual(self.store.get("profile", "account")["id"], 111)
        self.assertFalse(any(method == "get_activities" for method, _ in provider.calls))

    def test_old_activity_edit_refreshes_cached_details(self):
        provider = Provider()
        Sync(provider, self.store, self.config, lambda _: None).run(self.store.start_run(), today=TODAY)
        provider.calls.clear()
        provider.items[0]["distance"] = 6000
        Sync(provider, self.store, self.config, lambda _: None).run(self.store.start_run(), today=TODAY)
        self.assertTrue(any(method == "get_activity" for method, _ in provider.calls))

    def test_version_one_programme_start_remains_readable(self):
        self.store.db.execute("INSERT INTO settings VALUES ('program_start','2026-10-03')")
        self.store.db.commit()
        self.assertEqual(self.store.setting("program_start"), "2026-10-03")
