"""Routine imports stay incremental and page views cannot contact Garmin."""
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fivek.config import Config
from fivek.pipeline import report
from fivek.store import Store
from fivek.sync import Sync
from fivek.webapp import Application
from test_pipeline import Provider, TODAY


class DeltaSyncChecks(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(data_dir=Path(self.temp.name), start=TODAY-timedelta(days=60), request_delay=0)
        self.store = Store(self.config.data_dir)
        self.addCleanup(self.store.close)

    def run_sync(self, provider, **kwargs):
        run = self.store.start_run()
        result = Sync(provider, self.store, self.config, lambda _:None).run(run, today=TODAY, **kwargs)
        self.store.finish(run, 'complete', 'Fixture')
        return result

    def test_known_page_stops_discovery_and_retains_older_membership(self):
        class Pages(Provider):
            def fetch(self, method, *args):
                if method == 'get_activities':
                    self.calls.append((method,args))
                    return self.items[args[0]:args[0]+args[1]]
                return super().fetch(method,*args)
        provider=Pages()
        provider.items=[{**provider.items[0], 'activityId':i} for i in range(1,251)]
        self.run_sync(provider)
        self.assertEqual(len(self.store.get('activity_roster','current')),250)
        provider.calls.clear()
        self.run_sync(provider)
        self.assertEqual([args for method,args in provider.calls if method=='get_activities'],[(0,100)])
        self.assertEqual(len(self.store.get('activity_roster','current')),250)
        self.assertLessEqual(sum(method=='get_activity' for method,_ in provider.calls),20)

    def test_recent_days_obey_two_hour_and_daily_windows(self):
        from dataclasses import replace
        self.config=replace(self.config,start=TODAY-timedelta(days=7))
        provider=Provider()
        self.run_sync(provider)
        provider.calls.clear()
        self.run_sync(provider)
        self.assertFalse(any(method=='get_stats' for method,_ in provider.calls))
        # Keep ISO timestamps with timezone, matching production storage.
        from datetime import datetime, timezone
        stamp=(datetime.now(timezone.utc)-timedelta(hours=3)).isoformat()
        self.store.db.execute("UPDATE fetches SET attempted_at=? WHERE stream='daily'",(stamp,))
        self.store.db.commit()
        provider.calls.clear()
        self.run_sync(provider)
        self.assertEqual({args[0] for method,args in provider.calls if method=='get_stats'},
                         {TODAY.isoformat(),(TODAY-timedelta(days=1)).isoformat()})

    def test_changed_recent_activity_reloads_details_but_unchanged_does_not(self):
        provider=Provider()
        provider.items[0]['startTimeLocal']=TODAY.isoformat()+' 10:00:00'
        self.run_sync(provider)
        provider.calls.clear()
        self.run_sync(provider)
        self.assertFalse(any(method=='get_activity' for method,_ in provider.calls))
        provider.items[0]['distance']=6000
        self.run_sync(provider)
        self.assertTrue(any(method=='get_activity' for method,_ in provider.calls))

    def test_background_history_backfill_is_bounded_and_resumes(self):
        provider=Provider()
        self.run_sync(provider)
        days=len(self.store.records('daily'))
        self.assertEqual(days,42)  # 28 useful recent days, then 14 older days.
        self.run_sync(provider)
        self.assertEqual(len(self.store.records('daily')),56)

    def test_scheduled_backfill_is_healthy_and_eventually_completes(self):
        from fivek.pipeline import refresh
        provider=Provider()
        with patch('fivek.sync.local_today',return_value=TODAY), patch('fivek.pipeline.local_today',return_value=TODAY):
            first=refresh(provider,self.config,progress=lambda _:None)
            self.assertEqual(first['sync']['run']['status'],'backfilling')
            refresh(provider,self.config,progress=lambda _:None)
            final=refresh(provider,self.config,progress=lambda _:None)
        self.assertEqual(final['sync']['run']['status'],'complete')

    def test_reading_report_and_status_never_authenticates(self):
        report(self.store,self.config,TODAY)
        auth=unittest.mock.Mock(side_effect=AssertionError('Page read must never authenticate'))
        app=Application(self.config,auth_factory=auth)
        with patch('fivek.webapp.local_today',return_value=TODAY):
            app.get_report(); app.status(); app.get_report()
        auth.assert_not_called()

    def test_old_unsupported_metrics_stay_cached(self):
        key=(TODAY-timedelta(days=40)).isoformat()
        self.store.status('spo2',key,'unavailable','No device support')
        self.store.db.execute("UPDATE fetches SET attempted_at='2024-01-01T00:00:00+00:00'")
        self.store.db.commit()
        provider=Provider()
        Sync(provider,self.store,self.config,lambda _:None).fetch('spo2',key,'get_spo2_data',key)
        self.assertEqual(provider.calls,[])

    def test_january_goal_keeps_specific_phase_and_final_taper(self):
        plan=report(self.store,self.config,TODAY)['plan']
        self.assertEqual(plan['target_date'],'2027-01-31')
        self.assertEqual(plan['sessions'][-1]['kind'],'race')
        self.assertEqual(plan['progression']['taper_start'],'2027-01-21')
        self.assertTrue(any(w['phase']=='5K specific' for w in plan['weeks']))


if __name__=='__main__':
    unittest.main()
