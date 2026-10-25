from __future__ import annotations

from datetime import date, timedelta
from math import floor
from statistics import median
from .analysis import format_time
from .config import Config
from .strength import strength_workout


STRENGTH = "20–30 min: split squat, Romanian deadlift, calf raise, row and side plank; 2 sets of 6–10 controlled reps (calves 10–15), leaving 2–3 reps in reserve. Start light; reduce lower-body work if sore."


def quality_steps(budget_km: float, metrics: dict, specific: bool, programme_week: int | None = None) -> list[dict]:
    paces = [a["pace_seconds_km"] for a in metrics.get("activities", [])
             if a.get("kind") == "run" and a.get("pace_seconds_km") and 240 <= a["pace_seconds_km"] <= 720
             and a["date"] >= (date.fromisoformat(metrics.get("as_of", date.today().isoformat())) - timedelta(days=28)).isoformat()]
    # This estimates time available within a distance budget, not a target pace.
    available = budget_km * (median(paces) if paces else 360) / 60
    if available < 25:
        return []
    if programme_week is not None:
        # Alternate short repetitions, controlled tempo and 5K-specific work.
        # A full session must fit the observed pace/time budget, including rest.
        if specific:
            warm,cool,reps,work,recovery=8,8,5,2,2
        elif programme_week % 3 == 2 and programme_week>=5:
            warm,cool,reps,work,recovery=8,7,2,6,3
        elif programme_week % 3 == 1:
            warm,cool,reps,work,recovery=10,8,2,6,3
        elif programme_week % 3 == 2:
            warm,cool,reps,work,recovery=8,7,6,1,1
        else:
            warm,cool,reps,work,recovery=10,8,4,3,2
        total=warm+cool+reps*work+(reps-1)*recovery
        if total<=available:
            steps=[{'kind':'warmup','minutes':warm,'effort':'easy'}]
            for i in range(reps):
                steps.append({'kind':'work','minutes':work,'effort':'RPE 7–8' if specific else 'RPE 6–7'})
                if i<reps-1:
                    steps.append({'kind':'recovery','minutes':recovery,'effort':'easy jog or walk'})
            return steps+[{'kind':'cooldown','minutes':cool,'effort':'easy'}]
    warm, cool = (10, 10) if available >= 38 else (8, 7)
    work, recovery = (3 if not specific and available >= 38 else 2), 2
    reps = min(5 if specific else 4, int((available - warm - cool + recovery) // (work + recovery)))
    if reps < 3:
        return []
    spare = max(0, int(available * .9) - (warm + cool + reps * work + (reps - 1) * recovery))
    warm += min(15 - warm, spare // 2)
    cool += min(15 - cool, spare - spare // 2)
    steps = [{"kind": "warmup", "minutes": warm, "effort": "easy"}]
    for index in range(reps):
        steps.append({"kind": "work", "minutes": work, "effort": "RPE 7–8" if specific else "RPE 6–7"})
        if index < reps - 1:
            steps.append({"kind": "recovery", "minutes": recovery, "effort": "easy jog or walk"})
    steps.append({"kind": "cooldown", "minutes": cool, "effort": "easy"})
    return steps


def progression_review(metrics: dict, today: date, target: date, running_days: list[int], football_days: list[int], completed_weeks: int) -> dict:
    recovery = metrics["recovery"]["status"]
    base = metrics["baseline_weekly_km"] >= 8 and metrics.get("recent_running_days", 0) >= 6
    recent = metrics.get("weeks", [])[-2:]
    routine_days = min(2, len(running_days))
    recorded_days = [len({a["date"] for a in metrics.get("activities", []) if a["kind"] == "run"
                         and w.get("start", "9999") <= a["date"] <= w.get("end", "0000")}) for w in recent]
    routine = len(recorded_days) == 2 and all(count >= routine_days for count in recorded_days)
    confirmed = metrics.get("confirmed_benchmark")
    fresh_result = bool(confirmed and 0 <= (today - date.fromisoformat(confirmed["date"])).days <= 42)
    intensity=metrics.get('preferences',{}).get('football_intensity',{})
    hard_football=[d for d in football_days if intensity.get(str(d),'hard')=='hard']
    spaced_days = [d for d in running_days if all(min((d-f) % 7, (f-d) % 7) >= 2 for f in hard_football)]
    quality_schedule = len(running_days) >= (3 if len(hard_football) >= 2 else 2) and bool(spaced_days)
    days_left = (target - today).days
    milestones = [
        {"id": "recovery", "title": "Bring recovery into the decision", "status": "observed" if recovery == "normal" else "reassess",
         "detail": "Fresh recovery observations show no active flags." if recovery == "normal" else "Current recovery is " + recovery + ". Keep your watch synced and consider fatigue, soreness and pain before progressing.",
         "evidence": "Current recovery flags and observation freshness; no flags is not proof of readiness for a hard effort."},
        {"id": "routine", "title": "Repeat an achievable running routine", "status": "recorded" if routine else "building",
         "detail": f"The latest two seven-day windows contain {', '.join(str(count) for count in recorded_days) or 'no'} distinct running days. Repeat your available easy sessions within the plan's budget; do not catch up missed running.",
         "evidence": "At least " + str(routine_days) + " distinct running days in each window is a routine check, not proof of continuous running or easy effort."},
        {"id": "benchmark", "title": "Measure your current 5K", "status": "recent result" if fresh_result else "review when settled",
         "detail": "A measured result from the last six weeks is saved. Compare conditions and effort before setting the race target." if fresh_result else "After your easy routine is settled, and you can comfortably run for 30 minutes without pain, consider a measured 5K on a flat course. Use elapsed time and save the effort type. Replace a planned session and account for warm-up and cool-down; do not add a test on top of football or chase an old record.",
         "evidence": "A saved Garmin personal record or an ordinary ~5 km activity is historical or observational context. Your comfort and continuous-running ability are not inferred from watch duration."},
        {"id": "quality", "title": "Earn occasional controlled quality", "status": "base recorded" if base else "building base",
         "detail": f"{metrics['recent_run_count']} runs across {metrics.get('recent_running_days', 0)} dates and {metrics['baseline_weekly_km']:.1f} km/week recorded over 28 days; {completed_weeks} programme weeks elapsed. Quality needs at least six distinct running dates and 8 km/week, two programme weeks, fresh recovery, suitable spacing and enough time for every workout step. {len(hard_football)} football session(s) are scheduled as hard; actual recorded effort can change the recovery picture.",
         "evidence": "These are coaching gates for this product, not targets to reach by adding mileage or medical clearance. Schedule support: " + ("available" if quality_schedule else "limited") + "."},
    ]
    if recovery == "pause":
        title, action = "Resolve the pain signal first", "Pause impact training and update your check-in. Do not run, race or play through pain; seek assessment if it persists."
    elif recovery == "reduce":
        title, action = "Keep the next sessions easy", "Follow the reduced sessions or rest if you feel worse. Reassess recovery before adding quality or a benchmark."
    elif days_left <= 10:
        title, action = "Arrive at the start line fresh", "Follow the short taper sessions and reduce or skip football close to the goal. An extra fitness test now adds fatigue; use your existing reference and how you feel."
    elif recovery == "unknown":
        title, action = "Update recovery, then repeat the routine", "Keep your watch synced for the next automatic import. Keep running conversational and within the plan's budget while rebuilding regular practice."
    elif not routine or not base:
        title, action = "Repeat the easy routine", "Complete achievable easy sessions around football and log next-day soreness. Progress comes from repeated practice; keep the listed distance or time as a ceiling."
    elif not fresh_result:
        title, action = "Review a fresh measured 5K", "If your routine feels comfortable and recovery is settled, choose a measured effort to inform the goal. Replace a session, leave space from football and keep the week's total achievable."
    else:
        title, action = "Build from your measured result", "Keep easy running consistent and use only the controlled quality sessions the plan supplies. Review your result, recovery and football workload each week."
    return {"title": title, "action": action, "milestones": milestones,
            "routine_recorded": routine, "recent_measured_result": fresh_result,
            "benchmark_review_days": 42, "quality_schedule_supported": quality_schedule,
            "taper_start": (target - timedelta(days=10)).isoformat(),
            "note": "Milestones describe evidence and review points. The six-week result review is a coaching heuristic, not a requirement to repeat a test. These checks do not add workouts or guarantee a finishing time."}


def make_plan(metrics: dict, config: Config, today: date) -> dict:
    target = config.target
    baseline = metrics["baseline_weekly_km"]
    established = baseline >= 8 and metrics.get("recent_running_days", 0) >= 6
    program_start = date.fromisoformat(metrics.get("program_start", today.isoformat()))
    completed_program_weeks = max(0, ((today - timedelta(days=today.weekday())) - (program_start - timedelta(days=program_start.weekday()))).days // 7)
    starting = max(4, baseline) if baseline > 0 else 0
    followthrough = metrics.get("recent_prescription_followthrough", {})
    held_routine = False
    if (metrics["recovery"]["status"] == "normal" and followthrough.get("planned_days", 0) >= 2
            and followthrough.get("recorded_planned_days") == followthrough["planned_days"]
            and followthrough.get("planned_km", 0) >= 5
            and 0 < followthrough.get("recorded_km", 0) <= followthrough["planned_km"] * 1.1):
        # A completed prescribed routine can outlive a falling four-week mean.
        # Uncompleted forecasts and extra unplanned distance do not earn this hold.
        observed_routine = min(followthrough["recorded_km"], followthrough["planned_km"])
        held_routine = observed_routine > starting
        starting = max(starting, observed_routine)
    recent_weeks = metrics.get("weeks", [])[-2:]
    if (len(recent_weeks) == 2 and recent_weeks[-1].get("running_days", 0) >= 3
            and recent_weeks[-2].get("running_days", 0) >= 2 and metrics["recovery"]["status"] == "normal"):
        # Once practice is consistent, avoid holding volume down because older missed
        # weeks dilute the rolling average. Still cap the starting budget near baseline.
        starting = max(starting, min(recent_weeks[-1]["run_km"], baseline * 1.2))
    benchmark = metrics["benchmark"]
    metrics = {**metrics, "as_of": today.isoformat()}
    preferences = metrics.get("preferences", {})
    running_days = preferences.get("running_days", [2, 3, 5])
    football_days = preferences.get("football_days", [0, 6])
    gym = preferences.get("gym", True)
    returning=preferences.get('strength_experience')=='returning'
    gym_frequency=preferences.get('gym_sessions',2)
    upper_enabled=gym and (not returning or gym_frequency==3)
    football_intensity=preferences.get('football_intensity',{})
    hard_football=[d for d in football_days if football_intensity.get(str(d),'hard')=='hard']
    unallocated = [d for d in range(7) if d not in running_days and d not in football_days]
    rest_day = 4 if 4 in unallocated else unallocated[-1] if unallocated else None
    gym_day = next((d for d in unallocated if d != rest_day), None) if gym else None
    # Preserve the familiar Wednesday/Thursday/Saturday pattern by default.
    def match_gap(weekday):
        return min((min((weekday - f) % 7, (f - weekday) % 7) for f in hard_football), default=7)
    quality_day = max(running_days, key=lambda v: (match_gap(v), v == 3))
    long_day = 5 if 5 in running_days else running_days[-1]
    strength_day = next((v for v in running_days if v != quality_day and v != long_day), running_days[0])
    sessions = []
    week_start = today - timedelta(days=today.weekday())
    days_left = (target - today).days
    if days_left < 0:
        return {"target_date": target.isoformat(), "days_remaining": days_left, "sessions": [],
                "weeks": [], "strategy": "Target date has passed. Set FIVEK_TARGET_DATE to a future race date.",
                "target_time": None, "level": "goal finished", "rules": [],
                "pace_guidance": "Set a future goal to generate a plan."}
    total_weeks = max(1, ((target - week_start).days // 7) + 1)
    plan_weeks = []
    for index in range(total_weeks):
        start = week_start + timedelta(days=index * 7)
        remaining = (target - start).days
        taper = remaining <= 10
        phase = "taper" if taper else "foundation" if completed_program_weeks + index < 2 or (index == 0 and not established) else "5K specific" if remaining <= 35 else "build"
        week_phase = "taper begins" if 10 < remaining <= 16 else phase
        # Recalculated from actual rolling history on each refresh. No compounding from an old forecast.
        growth = min(1.35, 1.04 + 0.04 * index)
        cycle_index = completed_program_weeks + index
        km = round(starting * growth * (0.8 if cycle_index % 4 == 3 and not taper else 1) * (0.55 if taper else 1), 1)
        if starting < 10:
            km = None
        recorded_week = [a for a in metrics.get("activities", [])
                         if a["kind"] == "run" and start.isoformat() <= a["date"] <= min(today, start + timedelta(days=6)).isoformat()] if index == 0 else []
        actual_week_km = sum(a.get("km") or 0 for a in recorded_week)
        actual_week_minutes = sum(a.get("minutes") or 0 for a in recorded_week)
        easy_minutes = 15 if taper else min(35,20+2*min(cycle_index,8))
        long_minutes = 15 if taper else min(50,25+3*min(cycle_index,9))
        if cycle_index % 4 == 3 and not taper:
            easy_minutes = max(15,round(easy_minutes*.8))
            long_minutes = max(20,round(long_minutes*.8))
        minute_budget = sum(long_minutes if v == long_day and v != quality_day and v != strength_day else easy_minutes for v in running_days) if km is None else None
        remaining_budget = max(0, km - actual_week_km) if km is not None else None
        remaining_minutes = max(0, minute_budget - actual_week_minutes) if minute_budget is not None else None
        today_recorded_run = any(a["kind"] == "run" and a["date"] == today.isoformat() for a in metrics.get("activities", []))
        weights = {v: (0.4 if v == long_day and len(running_days) >= 3 else 0.3) for v in running_days}
        remaining_weights = sum(weight for weekday, weight in weights.items()
                                if start + timedelta(days=weekday) >= today and not (start + timedelta(days=weekday) == today and today_recorded_run))
        plan_weeks.append({"start": start.isoformat(), "phase": week_phase, "weekly_run_budget_km": km,
                           "recorded_run_km": round(actual_week_km, 2), "remaining_budget_km": remaining_budget,
                           "weekly_run_budget_minutes": minute_budget, "recorded_run_minutes": round(actual_week_minutes, 1),
                           "remaining_budget_minutes": round(remaining_minutes, 1) if remaining_minutes is not None else None,
                           "planned_run_km": km,
                           "conditional": index > 0,
                           "note": "Later phases depend on consistent running and recovery; quality is enabled only from observed readiness. Refresh weekly."})
        for weekday in range(7):
            day = start + timedelta(days=weekday)
            if day < today or day > target:
                continue
            to_race = (target - day).days
            day_taper = to_race <= 10
            session_budget = km * (.55 if day_taper and not taper else 1) if km else None
            fraction = weights.get(weekday, 0) / sum(weights.values())
            session_km = floor(session_budget * fraction * 10 + 1e-9) / 10 if session_budget else None
            budget_exhausted = False
            if index == 0 and session_km is not None and weekday in running_days and not (day == today and today_recorded_run):
                share = remaining_budget * weights[weekday] / remaining_weights if remaining_weights else 0
                session_km = floor(min(session_km, share) * 10 + 1e-9) / 10
                budget_exhausted = session_km < .5
            row = {"date": day.isoformat(), "weekday": day.strftime("%A"),
                   "phase": "taper" if day_taper else phase, "kind": "rest", "title": "Rest", "details": "Gentle walking or mobility as comfortable.",
                   "km": None, "minutes": None, "effort": "easy", "optional": False, "conditional": index > 0}
            if day == target:
                row.update(kind="race", title="New Year 5K" if (target.month, target.day) == (1, 1) else "Goal 5K", effort="race",
                           details="If healthy and recovered: 10–15 min easy warm-up, mobility and 3–4 relaxed strides. Start controlled, settle through kilometres 2–4, then increase effort in the final kilometre. Use a measured course and elapsed time.", km=5)
            elif weekday in football_days:
                effort=football_intensity.get(str(weekday),'hard')
                row.update(kind="football", title="Football · hard intervals" if effort=='hard' else "Football · lighter session", effort=effort,
                           details=("Treat this as the week's hard intermittent/interval session." if effort=='hard' else "Keep this match at moderate, social effort: limit all-out sprints and reduce minutes if needed. This is a lighter planned session, not a claim that the recorded match was easy.")+" Record it on your watch; actual duration, load and recovery still count. No additional running today. Reassess soreness afterward.")
                if to_race <= 5:
                    row.update(title="Football · taper decision", optional=True,
                               details="To prioritise your 5K, skip this match or substantially reduce minutes and sprinting. A full match this close to race day can leave fatigue; log what you actually do.")
            elif weekday == gym_day and not day_taper:
                row.update(kind="strength",title="Gym A · runner strength",minutes=35,effort="controlled strength",
                           details="5 min easy bike and joint warm-up. Complete the Gym A exercises with controlled technique. Assess soreness after football; use the upper-body alternative and omit loaded legs if sore. Finish with 5 min easy mobility.")
            elif weekday not in running_days and (weekday - 1) % 7 in football_days:
                row.update(title="Recovery after football", details="Rest or 15–25 min gentle walking and mobility. Check calf, hamstring and groin soreness before resuming running.")
            elif weekday == strength_day and weekday in running_days and weekday != quality_day:
                row.update(kind="easy", title="Easy run + gym", effort="RPE 2–3 / full sentences",
                           km=session_km, minutes=(15 if day_taper else easy_minutes) if session_km is None else None,
                           details="Comfortable conversational running; use walking breaks if needed. " + ("Light core and mobility only during taper." if day_taper else "Upper-body compound work can follow, without heavy legs before the next run." if upper_enabled else "Gentle mobility afterward."))
                if not upper_enabled:
                    row["title"] = "Easy aerobic run"
            elif weekday == quality_day and weekday in running_days:
                recent_paces = [a['pace_seconds_km'] for a in metrics.get('activities',[]) if a.get('kind')=='run' and a.get('pace_seconds_km') and 240<=a['pace_seconds_km']<=720 and a['date']>=(today-timedelta(days=28)).isoformat()]
                quality_km = min(session_km or easy_minutes/((median(recent_paces) if recent_paces else 360)/60), 8)
                steps = quality_steps(quality_km, metrics, phase == "5K specific", cycle_index)
                minimum_running_days = 3 if len(hard_football) >= 2 else 2
                conditional_quality = index>=2 and cycle_index>=2
                quality = (established or conditional_quality) and len(running_days) >= minimum_running_days and cycle_index >= 2 and cycle_index % 4 != 3 and not day_taper and bool(steps) and (metrics["recovery"]["status"] == "normal" or conditional_quality) and match_gap(weekday) >= 2
                reasons = []
                if not established:
                    reasons.append(f"Build consistency first: {metrics['recent_run_count']} runs across {metrics.get('recent_running_days', 0)} dates and {baseline:.1f} km/week recorded in the last 28 days; quality needs six distinct running dates and 8 km/week, plus space for every workout step.")
                if cycle_index < 2:
                    reasons.append("The first two programme weeks focus on easy running.")
                elif cycle_index % 4 == 3:
                    reasons.append("This is the fourth-week consolidation: keep the running easy and reduce volume.")
                if len(running_days) < minimum_running_days:
                    reasons.append(f"Keep your {len(running_days)} available running day(s) easy; this plan needs {minimum_running_days} running opportunities before adding quality alongside your football schedule.")
                if day_taper:
                    reasons.append("The final ten days prioritise freshness for the 5K.")
                if not steps and not day_taper:
                    reasons.append("The available distance budget is too small for warm-up, controlled repetitions and cool-down.")
                if metrics["recovery"]["status"] != "normal":
                    reasons.append("Quality needs fresh recovery observations without active flags; current recovery is " + metrics["recovery"]["status"] + ".")
                if match_gap(weekday) < 2:
                    reasons.append("This running day is too close to football for extra hard work.")
                row["decision_reasons"] = reasons or ["Recent running consistency, recovery, football spacing and the session budget support controlled quality today."]
                if quality:
                    reps = sum(s["kind"] == "work" for s in steps)
                    work_minutes = next(s["minutes"] for s in steps if s["kind"] == "work")
                    recovery_minutes=next((s['minutes'] for s in steps if s['kind']=='recovery'),0)
                    workout = f"{steps[0]['minutes']} min easy warm-up; {reps} × {work_minutes} min at controlled {'RPE 7–8' if phase == '5K specific' else 'RPE 6–7'}, with {recovery_minutes} min easy recovery between reps; {steps[-1]['minutes']} min easy cool-down."
                    label='5K intervals' if phase=='5K specific' else 'Tempo' if work_minutes>=5 else 'Intervals'
                    row.update(kind="quality", title=f"{label} · {reps} × {work_minutes} min", effort="controlled hard",
                               km=quality_km, steps=steps, estimated_minutes=sum(s["minutes"] for s in steps),
                               details=workout + " The listed distance is a maximum budget, not extra mileage to add after the steps. Shorten work repetitions if needed to leave room for the cool-down. Football already supplies demanding work.")
                    if conditional_quality:
                        row['readiness_note']='Conditional quality forecast: this requires at least six recorded running dates, 8 km/week, two programme weeks, fresh recovery without active flags and no unresolved pain. The session is rebuilt from actual observations before it becomes near-term; otherwise it becomes easy running.'
                else:
                    row.update(kind="easy", title="Easy aerobic run", effort="RPE 2–3 / full sentences",
                               km=session_km, minutes=(15 if day_taper else easy_minutes) if session_km is None else None,
                               details="Run comfortably. If new to running, alternate 2–3 min jogging with 1 min walking; stop before form deteriorates.")
            elif weekday not in running_days:
                row.update(title="Full rest day", optional=False,
                           details="No programmed training. Easy everyday walking is fine. Prioritise sleep, meals and recovery; do not turn today into a catch-up workout.")
            else:
                row.update(kind="easy", title="Longer easy run", effort="RPE 2–3 / full sentences",
                           km=session_km, minutes=(15 if day_taper else long_minutes) if session_km is None else None,
                           details="Conversational effort. Leave your legs fresh for Sunday football; shorten if sore. Do not chase pace or add a hard finish.")
            if budget_exhausted and row["kind"] in ("easy", "quality"):
                row.update(kind="rest", title="Running budget used · recover", km=None, minutes=None, effort="recovery",
                           details="Recorded running has used this week's forecast budget. Rest or walk comfortably; do not add mileage to catch up the plan.")
                row.pop("steps", None)
                row.pop("estimated_minutes", None)
            if row["kind"] == "easy" and (weekday - 1) % 7 in football_days:
                row["details"] += " This follows football: check soreness first, skip heavy legs at the gym, and choose walking or rest if you are not recovered."
            if to_race == 1:
                row.update(kind="rest", title="Race eve · rest", details="Rest; prepare route, shoes and kit. Gentle mobility only.", km=None, minutes=None, effort="easy")
            elif 1 < to_race <= 3 and row["kind"] in ("easy", "quality"):
                row.update(kind="easy", title="Short taper run", km=None, minutes=15,
                           details="15 min easy; optional 3 × 15 sec relaxed strides with full walking recovery, only if fresh.", effort="easy")
            if index == 0 and row["kind"] == "easy" and row["minutes"] and remaining_minutes is not None and not (day == today and today_recorded_run):
                share_minutes = remaining_minutes * weights[weekday] / remaining_weights if remaining_weights else 0
                row["minutes"] = min(row["minutes"], int(share_minutes))
                if row["minutes"] < 10:
                    row.update(kind="rest", title="Running time budget used · recover", minutes=None, effort="recovery",
                               details="Recorded running has used most of this week's run/walk time budget. Rest or walk comfortably; do not squeeze in extra running to catch up.")
            # Fresh recovery observations modify only near-term sessions, never months of forecasts.
            if day <= today + timedelta(days=2):
                recovery = metrics["recovery"]["status"]
                if recovery == "pause" and row["kind"] in ("easy", "quality", "football", "race", "strength"):
                    row.update(kind="rest", title="Pause impact training", km=None, minutes=None, effort="rest",
                               details="Pain reported: do not run, race or play football through pain. Reassess and seek assessment if pain persists.")
                elif recovery == "reduce" and row["kind"] in ("easy", "quality"):
                    row.update(kind="easy", title="Recovery-adjusted easy session", effort="very easy",
                               km=round(row["km"] * .65, 1) if row["km"] else None,
                               minutes=min(15, max(5, int((row["minutes"] or 20) * .65))) if row["km"] is None else None,
                               details="Recent recovery flags: easy effort only, about two-thirds of normal volume, or rest if you feel worse. No heavy lower-body gym work.")
                elif recovery == "reduce" and row["kind"] == "football":
                    row.update(optional=True, details="Recent recovery flags: reduce minutes and sprinting or skip the match; reassess how you feel before playing.")
                elif recovery == "reduce" and row["kind"] == "strength":
                    row.update(title="Gym A · recovery adjustment",minutes=20,effort="light technique",
                               details="Use the upper-body and trunk exercises only. No loaded lower-body work or failure sets while current recovery flags are active.")
                elif recovery == "reduce" and row["kind"] == "race":
                    row.update(effort="easy", title="Reassess race readiness", details="Recovery flags are active. Run easily or postpone the time trial if fatigued or unwell; do not chase a personal best.")
            if row["kind"] != "quality":
                row.pop("steps", None)
                row.pop("estimated_minutes", None)
            if day > today + timedelta(days=2) and row["kind"] in ("easy", "quality", "football", "race", "strength"):
                if metrics["recovery"]["status"] == "pause":
                    row["readiness_note"] = "This forecast assumes pain has resolved. Do not run, race or play through pain; add a new check-in and refresh before resuming."
                elif metrics["recovery"]["status"] == "reduce":
                    row["readiness_note"] = "This forecast assumes the current recovery flags settle. Refresh and reassess before this session; keep it easy or rest if fatigue or soreness persists."
            if row["kind"] == "easy" and row["minutes"] and starting < 10:
                row["details"] = (f"{row['minutes']} minutes total: 3 min brisk walking or easy jogging, then conversational running, then 3 min easy cool-down. If continuous running is uncomfortable, alternate 2 min easy running with 1 min walking within the same time budget. "
                                  + ("Upper-body gym follows; separate sessions by a few hours if preferred." if upper_enabled and weekday == strength_day and not day_taper else ""))
                if day_taper:
                    row["details"] += " Keep this short and relaxed during taper; skip fast strides while building run/walk consistency."
                if day <= today + timedelta(days=2) and metrics["recovery"]["status"] == "reduce":
                    row["details"] += " Recovery flags are active: walk or rest if you feel worse; no heavy lower-body gym work."
                if (weekday - 1) % 7 in football_days:
                    row["details"] += " Check soreness after football before starting."
            if row['kind']=='easy':
                duration=row.get('minutes') or max(8,round((row.get('km') or 0)*6))
                row['steps']=[{'kind':'warmup','minutes':3,'effort':'brisk walk or very easy jog'},
                              {'kind':'easy running','minutes':max(2,duration-6),'effort':'RPE 2–3; full sentences'},
                              {'kind':'cooldown','minutes':3,'effort':'walk or easy jog'}]
                row['estimated_minutes']=sum(step['minutes'] for step in row['steps'])
                row['decision_reasons']=row.get('decision_reasons',[])+[f"Recent running averages {baseline:.1f} km/week. Build consistent run exposure while football supplies hard efforts; no missed-session catch-up."]
            gym_upper = row['kind']=='easy' and weekday==strength_day and gym and not day_taper and (not returning or gym_frequency==3)
            gym_lower = row['kind']=='easy' and weekday==long_day and gym and returning and not day_taper
            if row['kind']=='strength' or gym_upper or gym_lower:
                reduced=day<=today+timedelta(days=2) and metrics['recovery']['status']=='reduce'
                gym_week=max(0,(day-program_start).days//7)
                observed_gyms=sum(a.get('sport')=='strength_training' and (today-timedelta(days=13)).isoformat()<=a['date']<=today.isoformat() for a in metrics.get('activities',[]))
                if index==0 and observed_gyms<2:
                    gym_week=min(gym_week,1)
                row['gym']=strength_workout(upper=gym_upper or reduced,week=gym_week,taper=day_taper,reduced=reduced,
                                           returning=returning,variant='C' if gym_upper else 'B' if gym_lower else 'A',deload=cycle_index%4==3)
                row['gym']['programme_key']='C' if returning and gym_upper else 'B' if gym_upper or gym_lower else 'A'
                if row['kind']=='strength':
                    row['minutes']=row['gym']['minutes']
                    row['title']=row['gym']['name']
                elif returning:
                    row['title']='Easy run + optional Gym C' if gym_upper else 'Longer easy run + Gym B'
                    row['details']+=' '+('Optional upper-compound Gym C is a third exposure, and the first to omit if recovery suffers.' if gym_upper else 'Gym B is the second baseline compound session. Separate it from the easy run by several hours; keep both submaximal.')
                row['total_minutes']=(row.get('estimated_minutes') or row.get('minutes') or 0)+(row['gym']['minutes'] if gym_upper or gym_lower else 0)
            row['fuel']="Eat a carbohydrate-containing meal before running or football and a normal meal with carbohydrate and protein afterward. On double-session days, eat and drink between sessions; do not compensate by skipping meals."
            outlook=metrics.get('athlete',{}).get('performance',{})
            if outlook.get('available'):
                row['pace_range_seconds_km']=outlook.get('easy_pace_seconds_km') if row['kind']=='easy' else None
                row['pace_note']=outlook.get('pace_note')
            row["status"] = "planned"
            if day == today:
                recorded = [a for a in metrics.get("activities", []) if a["date"] == day.isoformat()]
                recorded_runs = [a for a in recorded if a["kind"] == "run"]
                recorded_gym = [a for a in recorded if a.get('sport')=='strength_training']
                recorded_matches = [a for a in recorded if a["kind"] == "football"]
                checkin = next((v for v in metrics.get("feedback", []) if v["date"] == day.isoformat()), {})
                if recorded_runs and (row["kind"] != "race" or any((a["km"] or 0) >= 4.9 for a in recorded_runs)):
                    row.update(status="recorded", recorded_activity_ids=[a["id"] for a in recorded_runs],
                               actual_run_km=round(sum(a["km"] or 0 for a in recorded_runs), 2),
                               next_action="Running is already recorded today. Review what you did and recover; do not add a second run to satisfy this prescription. A recording does not prove that every workout step was completed.")
                    if metrics["recovery"]["status"] == "pause":
                        row["next_action"] = "Pain remains reported: pause impact training and do not run, race or play through pain. Your run is already recorded today; recover and update your check-in before resuming."
                elif row["kind"] == "football" and (recorded_matches or checkin.get("football_minutes") is not None):
                    skipped = checkin.get("football_minutes") == 0 and not recorded_matches
                    row.update(status="skipped" if skipped else "recorded",
                               recorded_activity_ids=[a["id"] for a in recorded_matches],
                               next_action="You logged no football today. Keep the next session as planned if you feel recovered; no catch-up match is needed." if skipped else "Your match recording or duration log is saved. Recover and reassess soreness before the next session; do not repeat the match.")
                if row.get('gym'):
                    row['gym']['status']='recorded' if recorded_gym else 'planned'
                    if recorded_gym and row['kind']=='strength':
                        row.update(status='recorded',recorded_activity_ids=[a['id'] for a in recorded_gym],next_action='Strength training is recorded today. Do not repeat the gym session. A recording does not verify every prescribed exercise.')
                    elif recorded_runs and row['kind']=='easy' and not recorded_gym:
                        row['next_action']='Running is recorded today; do not add a second run. '+row['gym']['name']+' remains planned if you feel recovered. Do it separately or omit it if tired; a run recording does not mark strength work done.'
            sessions.append(row)
    # Summaries describe the actual remaining prescriptions, including the goal effort.
    for week in plan_weeks:
        start = date.fromisoformat(week["start"])
        rows = [s for s in sessions if start <= date.fromisoformat(s["date"]) <= start + timedelta(days=6)
                and s["kind"] in ("easy", "quality", "race") and s.get("status") != "recorded"]
        week["planned_run_km"] = round(sum(s["km"] or 0 for s in rows), 1)
        all_week=[s for s in sessions if start<=date.fromisoformat(s['date'])<=start+timedelta(days=6)]
        week['training_days']=sum(s['kind']!='rest' for s in all_week)
        week['gym_sessions']=sum('gym' in s for s in all_week)
        week["planned_run_minutes"] = sum(s["minutes"] or 0 for s in rows)
        week["planned_training_km"] = round(sum(s["km"] or 0 for s in rows if s["kind"] != "race"), 1)
        week["planned_training_minutes"] = sum(s["minutes"] or 0 for s in rows if s["kind"] != "race")
        week["goal_run_km"] = sum(s["km"] or 0 for s in rows if s["kind"] == "race")
        if week["goal_run_km"]:
            week["note"] += " The goal 5K is listed separately from the training budget; race warm-up/cool-down are additional, adjusted to readiness."
    target_time = None
    if benchmark:
        target_time = {"reference": format_time(benchmark["seconds"]), "reference_date": benchmark["date"],
                       "guidance": "Aim to improve the measured reference; choose a race target after a fresh controlled benchmark. An optimal time cannot be guaranteed.",
                       "certainty": "Athlete-confirmed measured result; effort and conditions are self-reported." if benchmark.get("origin") == "athlete" else "Observed effort, not a validated maximal race or a performance prediction."}
    return {"target_date": target.isoformat(), "days_remaining": days_left,
            "strategy": "Build toward your best achievable 5K on " + target.strftime("%d %B %Y") + ". Running opportunities on " + ", ".join((date(2026, 10, 5) + timedelta(days=d)).strftime("%A") for d in running_days) + ". Establish consistent easy running, build from recorded volume, then use eligible 5K-specific sessions in the final five weeks and taper for the final ten days. Controlled quality requires an established base and fresh recovery. Strength supports running; football counts toward the workload. Rebuild from actual data every week.",
            "level": "established" if established else "foundation", "target_time": target_time,
            "progression": progression_review(metrics, today, target, running_days, football_days, completed_program_weeks),
            "volume_basis": {"rolling_baseline_km": baseline, "starting_weekly_km": round(starting, 2),
                             "held_completed_routine": held_routine,
                             "note": "A fully recorded prescribed week can preserve its observed distance when the rolling average falls; recovery must be normal and excess unplanned distance does not earn an increase."},
            "quality_readiness": {"eligible_base": established, "recent_runs": metrics["recent_run_count"],
                                  "recent_running_days": metrics.get("recent_running_days", 0), "required_running_days": 6,
                                  "weekly_km": baseline, "required_runs": 6, "required_weekly_km": 8,
                                  "completed_program_weeks": completed_program_weeks,
                                  "recovery": metrics["recovery"]["status"],
                                  "note": "These coaching gates control this plan; they are not medical clearance or a prediction of race performance."},
            "pace_guidance": "Easy: full sentences, RPE 2–3/10. Controlled quality: RPE 6–8/10, never sprint. No age-predicted heart-rate zones or unverified race paces.",
            "rules": ["No catch-up workouts after missed sessions.", "Forecast growth is a conservative heuristic, not an injury-proof rule; repeated soreness or missed weeks hold or reduce volume.", "Stop for pain; downgrade a session if fatigue, illness or football soreness changes how you move.", "Taper running in the final 10 days and reduce football close to race day.", "Height and age identify your profile; they do not determine running fitness."],
            "weekly_structure":{'training_days':6,'running_sessions':len(running_days),'football_sessions':len(football_days),
                                'gym_sessions':gym_frequency if returning and gym else 2 if gym and gym_day is not None else 0,'rest_weekday':rest_day,
                                'strength_experience':'returning (self-reported)' if returning else 'foundation',
                                'football_intensity':{str(d):football_intensity.get(str(d),'hard') for d in football_days},
                                'note':'Football counts toward six days. Returning-lifter mode has two main compound sessions and an optional third upper-compound exposure. Recovery flags and taper can reduce the schedule.' if returning else 'Football counts toward six days. Gym A has its own day; Gym B follows an easy run. Recovery flags and taper can reduce the schedule.'},
            "weeks": plan_weeks, "sessions": sessions}
