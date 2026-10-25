from datetime import date,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from fivek.config import Config
from fivek.analysis import analyse
from fivek.physiology import scale_readings,race_readings,body_outlook,performance_outlook
from fivek.plan import make_plan
from fivek.pipeline import report,calendar
from fivek.store import Store
from fivek.sync import Sync
from test_pipeline import Provider

TODAY=date(2026,10,26)  # Complete Monday–Sunday week.


class CoachingChecks(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config=Config(data_dir=Path(self.temp.name),start=TODAY,request_delay=0)
        self.store=Store(self.config.data_dir)
        self.addCleanup(self.store.close)

    def test_scale_grams_dates_and_latest_measurement_are_normalized(self):
        payload={'dateWeightList':[
            {'calendarDate':'2026-10-25','weight':78000,'muscleMass':33000,'bodyFat':18,'timestampGMT':100},
            {'calendarDate':'2026-10-25','weight':79000,'muscleMass':34000,'timestampGMT':200},
            {'calendarDate':'2027-01-01','weight':77000},
            {'calendarDate':'2026-10-24','weight':78}, # Wrong units must not become 0.078 kg.
        ]}
        rows=scale_readings(payload,TODAY)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['weight_kg'],79)
        self.assertEqual(rows[0]['muscle_kg'],34)

    def test_weight_target_is_maintenance_not_an_unearned_racing_weight(self):
        body=body_outlook([{'date':'2026-10-25','weight_kg':78,'body_fat_pct':18}],TODAY)
        self.assertEqual(body['target_range_kg'],[77.2,78.8])
        self.assertEqual(body['target_kind'],'Maintain and fuel training')
        stale=body_outlook([{'date':'2026-01-01','weight_kg':78}],TODAY)
        self.assertIsNone(stale['target_range_kg'])

    def test_six_days_include_football_three_runs_and_two_gyms(self):
        metrics=analyse(self.store,TODAY)
        plan=make_plan(metrics,self.config,TODAY)
        rows=plan['sessions'][:7]
        self.assertEqual(sum(r['kind']!='rest' for r in rows),6)
        self.assertEqual(sum(r['kind']=='football' for r in rows),2)
        self.assertEqual(sum(r['kind'] in {'easy','quality'} for r in rows),3)
        self.assertEqual(sum('gym' in r for r in rows),2)
        self.assertEqual(rows[4]['kind'],'rest')
        for r in rows:
            if 'gym' in r:
                self.assertTrue(all(e['sets'] and e['reps'] and e['rest_seconds'] for e in r['gym']['exercises']))
            if r['kind']=='easy':
                self.assertEqual(sum(s['minutes'] for s in r['steps']),r['minutes'])

    def test_gym_and_running_progress_and_fourth_week_consolidates(self):
        metrics=analyse(self.store,TODAY)
        plan=make_plan(metrics,self.config,TODAY)
        self.assertGreater(plan['weeks'][2]['planned_run_minutes'],plan['weeks'][0]['planned_run_minutes'])
        self.assertLess(plan['weeks'][3]['planned_run_minutes'],plan['weeks'][2]['planned_run_minutes'])
        first=next(r for r in plan['sessions'] if r['kind']=='strength')
        later=next(r for r in plan['sessions'][14:] if r['kind']=='strength')
        self.assertLess(first['gym']['exercises'][0]['sets'],later['gym']['exercises'][0]['sets'])
        self.assertIn('Gym A',calendar(plan))
        self.assertFalse(any(r['kind']=='quality' for r in plan['sessions'][:7]))
        future=[r for r in plan['sessions'][14:] if r['kind']=='quality']
        self.assertTrue(future)
        self.assertTrue(any(r['date']<'2026-12-01' for r in future))
        self.assertTrue(any(r['title'].startswith('Tempo') for r in future))
        self.assertTrue(any(r['title'].startswith('5K intervals') for r in future))
        self.assertTrue(all(r['conditional'] and 'forecast' in r['readiness_note'] for r in future))

    def test_fresh_prediction_is_separate_from_ordinary_run_and_weight(self):
        metrics=analyse(self.store,TODAY)
        metrics['benchmark']={'seconds':1800,'date':'2026-10-25'}
        self.assertFalse(performance_outlook(metrics,[],TODAY,self.config.target)['available'])
        rows=race_readings({'calendarDate':'2026-10-25','time5K':1400},TODAY)
        result=performance_outlook(metrics,rows,TODAY,self.config.target)
        self.assertEqual(result['current_seconds'],1400)
        self.assertLess(result['january_seconds'],1400)
        self.assertIn('unvalidated',result['method'])
        stale=[{'date':'2026-01-01','seconds':1200}]
        self.assertFalse(performance_outlook(metrics,stale,TODAY,self.config.target)['available'])
        metrics['recovery']['status']='pause'
        self.assertEqual(performance_outlook(metrics,rows,TODAY,self.config.target)['january_seconds'],1400)

    def test_scale_and_prediction_imports_are_cached_and_incremental(self):
        class AthleteProvider(Provider):
            def fetch(self,method,*args):
                if method=='get_body_composition':
                    self.calls.append((method,args))
                    return {'dateWeightList':[{'calendarDate':'2026-10-25','weight':78000}]}
                if method=='get_race_predictions':
                    self.calls.append((method,args))
                    return [{'calendarDate':'2026-10-25','time5K':1400}]
                return super().fetch(method,*args)
        provider=AthleteProvider()
        for _ in range(2):
            Sync(provider,self.store,self.config,lambda _:None).run(self.store.start_run(),today=TODAY)
        calls=[(name,args) for name,args in provider.calls if name in {'get_body_composition','get_race_predictions'}]
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[1][1][-1],'daily')
        self.assertEqual(calls[0][1][0],(TODAY-timedelta(days=89)).isoformat())
        self.assertEqual(self.store.get('body_measurements','2026-10-25')['weight_kg'],78)
        generated=report(self.store,self.config,TODAY)
        self.assertEqual(generated['metrics']['athlete']['performance']['current_seconds'],1400)
        from datetime import datetime,timezone
        stamp=(datetime.now(timezone.utc)-timedelta(hours=3)).isoformat()
        self.store.db.execute("UPDATE fetches SET attempted_at=? WHERE stream IN ('body_composition','race_predictions')",(stamp,))
        self.store.db.commit()
        provider.calls.clear()
        Sync(provider,self.store,self.config,lambda _:None).run(self.store.start_run(),today=TODAY)
        args=next(args for name,args in provider.calls if name=='get_body_composition')
        self.assertEqual(args[0],(TODAY-timedelta(days=6)).isoformat())

    def test_large_percentage_off_tiny_running_baseline_is_not_fatigue(self):
        raw={'activityId':1,'activityName':'Easy run','startTimeLocal':'2026-10-25 10:00:00','activityType':{'typeKey':'running'},'distance':4000,'duration':1500}
        self.store.put('activities','1',raw)
        metrics=analyse(self.store,TODAY)
        self.assertFalse(any('25%' in f for f in metrics['recovery']['flags']))


if __name__=='__main__':
    unittest.main()
