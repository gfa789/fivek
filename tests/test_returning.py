from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from fivek.analysis import analyse
from fivek.coaching import validate_preferences
from fivek.config import Config
from fivek.physiology import body_outlook
from fivek.plan import make_plan
from fivek.store import Store

TODAY=date(2026,10,26)
PREFERENCES={'strength_experience':'returning','gym_sessions':3,'football_intensity':{'0':'hard','6':'moderate'},'weight_strategy':'modest_reduction','weight_reference_kg':80}


class ReturningLifterChecks(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name));self.addCleanup(self.store.close)
        self.config=Config(data_dir=Path(self.temp.name))
        self.metrics=analyse(self.store,TODAY)
        self.metrics['preferences']=PREFERENCES.copy()

    def test_compounds_two_main_days_optional_third_and_one_rest(self):
        rows=make_plan(self.metrics,self.config,TODAY)['sessions'][:7]
        self.assertEqual(sum(r['kind']!='rest' for r in rows),6)
        self.assertEqual(rows[0]['effort'],'hard')
        self.assertEqual(rows[6]['effort'],'moderate')
        self.assertEqual(rows[4]['kind'],'rest')
        self.assertEqual(sum('gym' in r for r in rows),3)
        self.assertFalse(rows[1]['gym']['optional'])
        self.assertTrue(rows[2]['gym']['optional'])
        self.assertFalse(rows[5]['gym']['optional'])
        self.assertEqual(rows[1]['gym']['exercises'][0]['name'],'Barbell back squat')
        self.assertEqual(rows[1]['gym']['exercises'][0]['sets'],3)
        self.assertIn('deadlift',rows[5]['gym']['exercises'][0]['name'])
        self.assertIn('self-reported',rows[1]['gym']['experience_basis'])

    def test_two_day_variant_does_not_claim_an_extra_gym(self):
        self.metrics['preferences']={**PREFERENCES,'gym_sessions':2}
        rows=make_plan(self.metrics,self.config,TODAY)['sessions'][:7]
        self.assertEqual(sum('gym' in r for r in rows),2)
        self.assertNotIn('gym',rows[2]['title'].lower())
        self.assertNotIn('gym follows',rows[2]['details'].lower())

    def test_recovery_flags_override_returning_experience(self):
        self.metrics['recovery']['status']='reduce'
        row=make_plan(self.metrics,self.config,TODAY)['sessions'][1]
        self.assertNotIn('Barbell back squat',[e['name'] for e in row['gym']['exercises']])
        self.metrics['recovery']['status']='pause'
        rows=make_plan(self.metrics,self.config,TODAY)['sessions'][:3]
        self.assertTrue(all(r['kind']=='rest' for r in rows))

    def test_reduction_is_anchored_and_does_not_ratchet_down(self):
        rows=[{'date':'2026-10-25','weight_kg':80,'bmi':24}]
        body=body_outlook(rows,TODAY,PREFERENCES,'normal',self.config.target)
        self.assertEqual(body['target_range_kg'],[78.4,79.2])
        lower=body_outlook([{'date':'2026-10-25','weight_kg':79,'bmi':23.7}],TODAY,PREFERENCES,'normal',self.config.target)
        self.assertEqual(lower['target_range_kg'],[78.4,79.2])
        self.assertIn('maintain',lower['target_kind'])
        reduced=body_outlook(rows,TODAY,PREFERENCES,'reduce',self.config.target)
        self.assertEqual(reduced['target_kind'],'Maintain and fuel training')
        taper=body_outlook(rows,date(2026,10,27),PREFERENCES,'normal',date(2026,11,5))
        self.assertEqual(taper['target_kind'],'Maintain and fuel training')

    def test_preferences_reject_invalid_training_and_weight_fields(self):
        self.assertEqual(validate_preferences(PREFERENCES,TODAY)['gym_sessions'],3)
        for body in [{'gym_sessions':True},{'strength_experience':[]},{'football_intensity':{'6':[]}},{'weight_reference_kg':float('nan')}]:
            with self.assertRaises(ValueError):
                validate_preferences(body,TODAY)
