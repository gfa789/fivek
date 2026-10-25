"""Two complementary strength exposures within a six-day mixed programme."""


def strength_workout(upper=False, week=0, taper=False, reduced=False, returning=False, variant=None, deload=False):
    if returning and not reduced:
        return compound_workout(variant or ('C' if upper else 'A'),taper,deload)
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


def compound_workout(variant, taper=False, deload=False):
    programmes={
        'A':('Gym A · squat, bench & row',[
            ('Barbell back squat',3,'5','Brace; use your familiar comfortable depth'),
            ('Barbell bench press',3,'5','Controlled touch; use safeties or a spotter'),
            ('Barbell or chest-supported row',3,'6–8','No torso heaving'),
            ('Romanian deadlift',2,'6–8','Controlled hinge; stop before back position changes'),
            ('Standing calf raise',2,'10–12','Full comfortable range'),
        ]),
        'B':('Gym B · deadlift, press & pull',[
            ('Conventional or trap-bar deadlift',2,'5','Use the familiar variation; reset each repetition'),
            ('Standing overhead press',3,'5','Brace; no excessive backward lean'),
            ('Pull-up or lat pulldown',3,'6–8','Full controlled repetitions'),
            ('Front squat or split squat',2,'6','Moderate technique work; no grinding'),
            ('Dumbbell bench press',2,'6–8','Leave repetitions in reserve'),
        ]),
        'C':('Gym C · optional upper compounds',[
            ('Bench press',3,'6','Lighter than Gym A; smooth repetitions'),
            ('Chest-supported row',3,'8','Controlled pull and lowering'),
            ('Seated dumbbell overhead press',2,'6–8','Avoid failure or forced repetitions'),
            ('Side plank',2,'25–40 sec each side','Keep trunk aligned'),
        ]),
    }
    name,rows=programmes[variant]
    exercises=[{'name':name,'sets':1 if taper else max(1,sets-1) if deload else sets,
                'reps':reps,'rest_seconds':180 if index<3 and variant!='C' else 120,'cue':cue}
               for index,(name,sets,reps,cue) in enumerate(rows)]
    return {'name':name,'minutes':25 if taper else 40 if deload else 40 if variant=='C' else 60,
            'exercises':exercises,'optional':variant=='C',
            'experience_basis':'Returning lifter: prior compound-lifting experience is self-reported, not inferred from watch data.',
            'effort':'RPE 6 during taper/deload; no grinding' if taper or deload else 'Working sets at RPE 7–8; leave 2–3 repetitions in reserve',
            'progression':'Start with familiar movements and current submaximal working weights, not old personal records. Use ramp-up sets before each main lift. When all working sets meet the prescribed reps with at least two reps in reserve and sound technique, add the smallest available increment next exposure (about 2–5%). If reps are missed, repeat the load; after two difficult exposures reduce it 5–10%. No max testing is required. Working kilograms cannot be inferred from Garmin.',
            'adjustment':'Tuesday follows hard Monday football: use the current RPE ceiling, and replace loaded leg lifts with upper-body work if sore. Separate Saturday lifting and the easy run by several hours where practical; shorten the run or reduce lower-body work if either session suffers. Gym C is the first session to omit when fatigued. Every fourth week reduce working sets; during the final taper reduce sets and avoid new movements.'}
