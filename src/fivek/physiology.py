"""Observed scale/watch signals and explicitly conditional performance outlooks."""
from datetime import date, timedelta
from statistics import median, mean
from .analysis import bounded


def valid_date(value, today):
    try:
        day=date.fromisoformat(str(value)[:10])
        return day.isoformat() if day<=today else None
    except (ValueError,TypeError):
        return None


def scale_readings(payload, today):
    rows=payload.get('dateWeightList',[]) if isinstance(payload,dict) else []
    output={}
    for raw in rows if isinstance(rows,list) else []:
        if not isinstance(raw,dict):
            continue
        day=valid_date(raw.get('calendarDate'),today)
        grams=bounded(raw.get('weight'),20000,400000)
        if not day or grams is None:
            continue
        stamp=bounded(raw.get('timestampGMT'),0,253402214400000) or 0
        if day in output and output[day]['timestamp']>=stamp:
            continue
        muscle=bounded(raw.get('muscleMass'),1000,200000)
        bone=bounded(raw.get('boneMass'),100,20000)
        output[day]={'date':day,'weight_kg':round(grams/1000,3),
                     'body_fat_pct':bounded(raw.get('bodyFat'),1,70),
                     'muscle_kg':round(muscle/1000,3) if muscle else None,
                     'bone_kg':round(bone/1000,3) if bone else None,
                     'body_water_pct':bounded(raw.get('bodyWater'),10,90),
                     'bmi':bounded(raw.get('bmi'),10,70),'timestamp':stamp,
                     'source':str(raw.get('sourceType') or 'Garmin scale')[:40]}
    return sorted(output.values(),key=lambda r:r['date'])


def race_readings(payload, today):
    rows=payload if isinstance(payload,list) else [payload] if isinstance(payload,dict) else []
    output={}
    for row in rows:
        if not isinstance(row,dict):
            continue
        day=valid_date(row.get('calendarDate'),today)
        seconds=bounded(row.get('time5K'),600,7200)
        if day and seconds is not None:
            output[day]={'date':day,'seconds':round(seconds),'source':'Garmin race predictor'}
    return sorted(output.values(),key=lambda r:r['date'])


def body_outlook(rows, today):
    rows=sorted([r for r in rows if r['date']<=today.isoformat()],key=lambda r:r['date'])
    if not rows:
        return {'available':False,'readings':[],'note':'No scale measurements imported yet. No weight target can be inferred.'}
    latest=rows[-1]
    recent=[r for r in rows if r['date']>=(today-timedelta(days=6)).isoformat()]
    previous=[r for r in rows if (today-timedelta(days=27)).isoformat()<=r['date']<(today-timedelta(days=6)).isoformat()]
    average=round(mean(r['weight_kg'] for r in recent),2) if recent else None
    previous_average=round(mean(r['weight_kg'] for r in previous),2) if previous else None
    stale=(today-date.fromisoformat(latest['date'])).days>14
    # A maintenance band is a training/fuelling reference, never an inferred
    # ideal racing weight or a body-fat-based prescription to lose tissue.
    target=([round(average*.99,1),round(average*1.01,1)] if average is not None and not stale else None)
    return {'available':True,'latest':latest,'readings':rows,'seven_day_kg':average,
            'seven_day_count':len(recent),'previous_21_day_kg':previous_average,
            'change_kg':round(average-previous_average,2) if average is not None and previous_average is not None else None,
            'stale':stale,'target_range_kg':target,'target_kind':'Maintain and fuel training',
            'note':'The ±1% maintenance band is a planning reference around your seven-day mean, not an ideal racing weight or a medical target. Scale body-fat, muscle and water estimates vary with hydration. Lower weight is not automatically faster; no race-time benefit is assumed from weight loss. Fuel runs, gym and football; persistent fatigue, rapid unintentional loss or deteriorating recovery warrants nutrition review.'}


def recovery_context(metrics,today):
    daily=[d for d in metrics['daily'] if d['date']<=(today.isoformat())]
    def compare(key):
        recent=[d[key] for d in daily if d.get(key) is not None and d['date']>=(today-timedelta(days=6)).isoformat()]
        prior=[d[key] for d in daily if d.get(key) is not None and (today-timedelta(days=34)).isoformat()<=d['date']<(today-timedelta(days=6)).isoformat()]
        return {'recent':round(mean(recent),1) if recent else None,'baseline':round(median(prior),1) if len(prior)>=7 else None,'readings':len(recent)}
    latest=next((d for d in reversed(daily) if any(d.get(k) is not None for k in ['hrv_ms','readiness','acute_load','vo2_max'])),{})
    ratio=round(latest['acute_load']/latest['chronic_load'],2) if latest.get('acute_load') is not None and latest.get('chronic_load') else None
    return {'latest':latest,'sleep':compare('sleep_hours'),'hrv':compare('hrv_ms'),'resting_hr':compare('resting_hr'),
            'load_ratio':ratio,'note':'Load ratio describes Garmin device units, not an injury-risk threshold. Baselines exclude the latest seven days. Device scores do not clear a hard session.'}


def performance_outlook(metrics, rows, today, target):
    fresh=[r for r in rows if (today-timedelta(days=7)).isoformat()<=r['date']<=today.isoformat()]
    confirmed=metrics.get('confirmed_benchmark')
    measured=bool(confirmed and confirmed.get('kind') in {'race','time_trial'} and 0<=(today-date.fromisoformat(confirmed['date'])).days<=42)
    current=confirmed['seconds'] if measured else fresh[-1]['seconds'] if fresh else None
    source='Recent measured 5K' if measured else 'Garmin race predictor' if fresh else None
    if current is None:
        return {'available':False,'history':rows,'note':'A fresh Garmin race prediction or measured race/time trial is needed. Ordinary easy runs are shown separately and are not treated as maximal races.'}
    weeks=max(0,(target-today).days/7)
    consistency=metrics.get('recent_running_days',0)
    recovery=metrics['recovery']['status']
    # This scenario is deliberately not represented as a fitted/validated model.
    # Baseline prediction is a device estimate; an ordinary run is not a race.
    improvement=0 if recovery=='pause' or weeks<2 else min(.04,weeks*.0025)
    central=round(current*(1-improvement))
    spread=.07 if measured and consistency>=8 else .12
    pace=current/5
    recent_values=[r['seconds'] for r in rows if (today-timedelta(days=6)).isoformat()<=r['date']<=today.isoformat()]
    prior_values=[r['seconds'] for r in rows if (today-timedelta(days=27)).isoformat()<=r['date']<(today-timedelta(days=6)).isoformat()]
    trend=round(mean(recent_values)-median(prior_values)) if len(recent_values)>=3 and len(prior_values)>=7 else None
    return {'available':True,'current_seconds':round(current),'source':source,
            'source_date':confirmed['date'] if measured else fresh[-1]['date'],
            'current_range_seconds':[round(current*(1-spread)),round(current*(1+spread))],
            'january_seconds':central,'january_range_seconds':[round(current*(1-min(.06,improvement+.02))),round(current*1.05)],
            'confidence':'Low · conditional planning scenario','target_date':target.isoformat(),'history':rows,
            'recent_trend_seconds':trend,'trend_basis':'Last seven-day mean minus preceding 21-day median; positive means the Garmin estimate has slowed, negative means faster.',
            'assumptions':'Complete at least 80% of prescribed running, keep two strength exposures and football within recovery capacity, remain healthy, and taper. Six training days does not mean six hard days.',
            'method':'January central scenario assumes 0.25% improvement per remaining week, capped at 4%; stretch is capped at 6%, interrupted-training scenario is 5% slower. These are unvalidated planning assumptions, not confidence intervals or a personalised statistical prediction. No weight-loss speed bonus is included.',
            'easy_pace_seconds_km':[round(pace+75),round(pace+135)],
            'tempo_pace_seconds_km':[round(pace+20),round(pace+40)],
            'fivek_pace_seconds_km':round(pace),
            'pace_note':'Device-derived starting ranges. Use conversational effort for easy runs and controlled RPE for quality; until checked against a measured effort, do not chase the watch pace.'}


def athlete_insights(store,metrics,today,target):
    body=body_outlook(list(store.records('body_measurements').values()),today)
    races=sorted(store.records('race_estimates').values(),key=lambda r:r['date'])
    return {'body':body,'recovery':recovery_context(metrics,today),
            'performance':performance_outlook(metrics,races,today,target)}
