"""Two complementary strength exposures within a six-day mixed programme."""


def strength_workout(upper=False, week=0, taper=False, reduced=False):
    sets=1 if taper else 2 if week<2 or reduced else 3
    exercises=([
        ('Chest-supported row','8–12','Keep torso still'),
        ('Dumbbell bench press or press-up','8–12','Controlled lowering'),
        ('Lat pulldown','8–12','No swinging'),
        ('Dead bug','6–8 each side','Slow, steady breathing'),
        ('Side plank','20–30 sec each side','Stop before form breaks'),
    ] if upper else [
        ('Goblet squat','6–8','Use a comfortable depth'),
        ('Romanian deadlift','6–8','Hinge at hips; controlled lowering'),
        ('Split squat','6 each side','Use support if needed'),
        ('Standing calf raise','10–15','Pause at the top; lower slowly'),
        ('Seated row','8–12','Keep shoulders relaxed'),
        ('Side plank','20–30 sec each side','Stop before form breaks'),
    ])
    return {'name':'Gym B · upper body & trunk' if upper else 'Gym A · runner strength',
            'minutes':15 if taper else (25 if sets==3 else 20) if upper else (45 if sets==3 else 35),
            'exercises':[{'name':name,'sets':sets,'reps':reps,'rest_seconds':60 if upper else 90,'cue':cue} for name,reps,cue in exercises],
            'effort':'Leave 3–4 repetitions in reserve' if reduced or week<2 else 'Leave 2–3 repetitions in reserve',
            'progression':'First two weeks: learn movements with light loads. Then add repetitions within the range; increase by the smallest available weight only after two comfortable exposures with sound technique and normal next-day recovery. Never increase load and sets together. No invented kilogram prescription; working weights are not recorded.',
            'adjustment':'After football, assess calf, groin and hamstring soreness. If sore, do the upper-body alternative and omit loaded legs. During taper halve normal sets and keep loads familiar; no new exercises or failure sets.'}
