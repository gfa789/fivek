from __future__ import annotations

import math
import json
import os
import re
from datetime import date, datetime, timedelta
from statistics import mean, median
from typing import Any
from zoneinfo import ZoneInfo

from .store import Store


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def nested(data: Any, *keys):
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def bounded(value: Any, minimum: float, maximum: float) -> float | None:
    value = number(value)
    return value if value is not None and minimum <= value <= maximum else None


def mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def average(values):
    values = [v for v in values if v is not None]
    return round(mean(values), 2) if values else None


def running_records(payload, today: date) -> list[dict]:
    if isinstance(payload, dict):
        payload = payload.get("personalRecords", payload.get("records", []))
    if not isinstance(payload, list):
        return []
    kinds = {1: ("1 km", 60, 3600), 2: ("1 mile", 90, 5400), 3: ("5 km", 600, 7200),
             4: ("10 km", 1200, 21600), 5: ("Half marathon", 2400, 86400),
             6: ("Marathon", 4800, 172800), 7: ("Longest run", 1, 1000000)}
    records = []
    for item in payload:
        item = mapping(item)
        kind = item.get("typeId")
        if type(kind) is not int or kind not in kinds or item.get("activityType") != "running" or item.get("status") != "ACCEPTED":
            continue
        label, minimum, maximum = kinds[kind]
        value = bounded(item.get("value"), minimum, maximum)
        if value is None:
            continue
        day, date_basis = None, None
        stamp = number(item.get("prStartTimeGmt"))
        if stamp and stamp <= 253402214400000:
            try:
                day = datetime.fromtimestamp(stamp / 1000, ZoneInfo(os.getenv("FIVEK_TIMEZONE", "Europe/London"))).date()
                date_basis = "Record start timestamp in the configured time zone"
            except (ValueError, OSError, OverflowError):
                pass
        if day is None:
            for field in ("prStartTimeLocalFormatted", "activityStartDateTimeLocalFormatted", "prStartTimeGmtFormatted"):
                try:
                    day = date.fromisoformat(str(item.get(field, ""))[:10])
                    date_basis = field
                    break
                except ValueError:
                    pass
        if day and day > today:
            continue
        activity_id = str(item.get("activityId", ""))
        activity_id = activity_id if activity_id.isascii() and activity_id.isdigit() and len(activity_id) <= 24 and int(activity_id) > 0 else None
        records.append({"type_id": kind, "label": label, "date": day.isoformat() if day else None,
                        "date_basis": date_basis, "seconds": round(value, 3) if kind != 7 else None,
                        "distance_km": round(value / 1000, 3) if kind == 7 else None,
                        "activity_id": activity_id, "origin": "garmin", "status": "accepted",
                        "basis": "Saved accepted Garmin Connect running record. It may be a segment or edited entry; course and effort are unverified. Historical records do not establish current race fitness."})
    return sorted(records, key=lambda item: (item["type_id"], item["seconds"] if item["seconds"] is not None else -(item["distance_km"] or 0)))


def activities(store: Store) -> list[dict]:
    details = store.records("activity")
    roster = store.records("activity_roster").get("current")
    result = []
    for key, raw in store.records("activities").items():
        if not isinstance(raw, dict):
            continue
        if roster is not None and key not in roster:
            continue
        detail = details.get(key) or {}
        summary = mapping(nested(detail, "summaryDTO"))
        data = {**raw, **{k: v for k, v in summary.items() if v is not None}}
        sport = nested(raw, "activityType", "typeKey")
        sport = sport if isinstance(sport, str) else "unknown"
        title = str(raw.get("activityName", "Untitled activity"))
        football = sport in {"soccer", "football", "futsal"} or any(
            word in title.lower() for word in ("football", "soccer", "5 aside", "5-a-side", "five-a-side", "futsal"))
        run = sport in {"running", "treadmill_running", "trail_running", "track_running", "street_running", "indoor_running", "ultra_run", "virtual_run"} and not football
        distance = number(data.get("distance"))
        duration = number(data.get("duration"))
        moving = number(data.get("movingDuration"))
        elapsed = number(data.get("elapsedDuration"))
        day = str(raw.get("startTimeLocal") or raw.get("startTimeGMT") or "")[:10]
        try:
            day = date.fromisoformat(day).isoformat()
        except ValueError:
            continue
        result.append({
            "id": key, "date": day, "name": title, "sport": sport,
            "kind": "football" if football else "run" if run else "other",
            "km": round(distance / 1000, 3) if distance is not None else None,
            "minutes": round(duration / 60, 2) if duration is not None else None,
            "duration_seconds": duration, "moving_seconds": moving, "elapsed_seconds": elapsed,
            "pace_seconds_km": round(duration / (distance / 1000), 1) if run and duration and distance else None,
            "average_hr": bounded(data.get("averageHR"), 25, 250), "max_hr": bounded(data.get("maxHR"), 25, 250),
            "ascent_m": number(data.get("elevationGain")),
            "aerobic_effect": number(data.get("aerobicTrainingEffect")),
            "anaerobic_effect": number(data.get("anaerobicTrainingEffect")),
            "cadence": number(data.get("averageRunCadence")),
            "training_load": number(data.get("activityTrainingLoad")),
        })
    return sorted(result, key=lambda a: (a["date"], a["id"]), reverse=True)


def daily(store: Store) -> list[dict]:
    streams = {name: store.records(name) for name in ("daily", "sleep", "hrv", "readiness", "stress", "body_battery", "fitness", "training")}
    days = set().union(*(set(v) for v in streams.values()))
    result = []
    for day in sorted(days):
        try:
            if date.fromisoformat(day).isoformat() != day:
                continue
        except (ValueError, TypeError):
            continue
        stats = mapping(streams["daily"].get(day))
        sleep = mapping(streams["sleep"].get(day))
        hrv = mapping(streams["hrv"].get(day))
        fitness = streams["fitness"].get(day) or []
        if isinstance(fitness, list):
            fitness = fitness[0] if fitness else {}
        fitness = mapping(fitness)
        readiness = streams["readiness"].get(day) or {}
        if isinstance(readiness, list):
            readiness = next((r for r in readiness if isinstance(r, dict) and r.get("inputContext") == "AFTER_WAKEUP_RESET"), readiness[0] if readiness else {})
        readiness = mapping(readiness)
        seconds = bounded(nested(sleep, "dailySleepDTO", "sleepTimeSeconds"), 1, 24 * 3600)
        battery = streams["body_battery"].get(day) or []
        battery = battery[0] if isinstance(battery, list) and battery else battery
        battery = mapping(battery)
        battery_values = battery.get("bodyBatteryValuesArray")
        battery_values = battery_values if isinstance(battery_values, list) else []
        battery_values = [bounded(v[-1], 0, 100) for v in battery_values if isinstance(v, list) and len(v) >= 2]
        battery_values = [v for v in battery_values if v is not None]
        # A provider can return a neighbouring date when a requested day is missing.
        stats_valid = stats.get("calendarDate") in (None, day)
        sleep_valid = nested(sleep, "dailySleepDTO", "calendarDate") in (None, day)
        hrv_valid = nested(hrv, "hrvSummary", "calendarDate") in (None, day)
        readiness_valid = readiness.get("calendarDate") in (None, day)
        battery_valid = battery.get("date") in (None, day)
        fitness_valid = nested(fitness, "generic", "calendarDate") in (None, day)
        hrv_status = nested(hrv, "hrvSummary", "status")
        hrv_status = hrv_status.upper() if isinstance(hrv_status, str) else None
        statuses = mapping(nested(streams["training"].get(day), "mostRecentTrainingStatus", "latestTrainingStatusData"))
        statuses = [v for v in statuses.values() if isinstance(v, dict)]
        training = next((v for v in statuses if v.get("primaryTrainingDevice") is True and v.get("calendarDate") == day), None)
        training = training or next((v for v in statuses if v.get("calendarDate") == day), {})
        training_valid = bool(training) or not any(v.get("calendarDate") is not None for v in statuses)
        load = mapping(training.get("acuteTrainingLoadDTO"))
        phrase = training.get("trainingStatusFeedbackPhrase")
        status_label = re.sub(r"_\d+$", "", phrase).replace("_", " ").title() if isinstance(phrase, str) and len(phrase) <= 100 else None
        stress = mapping(streams["stress"].get(day))
        stress_valid = stress.get("calendarDate") in (None, day)
        result.append({
            "date": day, "resting_hr": bounded(stats.get("restingHeartRate"), 25, 120) if stats_valid else None,
            "steps": bounded(stats.get("totalSteps"), 0, 200000) if stats_valid else None,
            "sleep_hours": round(seconds / 3600, 2) if seconds is not None and sleep_valid else None,
            "sleep_score": bounded(nested(sleep, "dailySleepDTO", "sleepScores", "overall", "value"), 0, 100) if sleep_valid else None,
            "hrv_ms": bounded(nested(hrv, "hrvSummary", "lastNightAvg"), 1, 300) if hrv_valid else None,
            "hrv_status": hrv_status if hrv_valid and hrv_status in {"BALANCED", "LOW", "UNBALANCED", "POOR"} else None,
            "readiness": bounded(readiness.get("score"), 0, 100) if readiness_valid else None,
            "acute_load": number(load.get("dailyTrainingLoadAcute")) if load.get("dailyTrainingLoadAcute") is not None else number(readiness.get("acuteLoad")) if readiness_valid else None,
            "chronic_load": number(load.get("dailyTrainingLoadChronic")),
            "training_status": status_label if status_label and not status_label.startswith("No Status") else None,
            "vo2_max": (bounded(nested(fitness, "generic", "vo2MaxPreciseValue"), 10, 100) or bounded(nested(fitness, "generic", "vo2MaxValue"), 10, 100)) if fitness_valid else None,
            "stress": bounded(stress.get("avgStressLevel"), 0, 100) if stress_valid and stress.get("avgStressLevel") is not None else bounded(stats.get("averageStressLevel"), 0, 100) if stats_valid else None,
            "body_battery_charged": number(battery.get("charged")) if battery_valid else None,
            "body_battery_drained": number(battery.get("drained")) if battery_valid else None,
            "body_battery_min": min(battery_values) if battery_values and battery_valid else None,
            "body_battery_max": max(battery_values) if battery_values and battery_valid else None,
            "body_battery_last": battery_values[-1] if battery_values and battery_valid else None,
            "date_mismatches": [name for name, valid in (("daily", stats_valid), ("sleep", sleep_valid), ("hrv", hrv_valid), ("readiness", readiness_valid), ("body_battery", battery_valid), ("fitness", fitness_valid), ("training", training_valid), ("stress", stress_valid)) if not valid],
        })
    return result


def football_summary(all_activities: list[dict], feedback: dict, today: date, football_days: list[int]) -> dict:
    rows = []
    for offset in reversed(range(28)):
        day = today - timedelta(days=offset)
        key = day.isoformat()
        recorded = [a for a in all_activities if a["kind"] == "football" and a["date"] == key]
        entry = feedback.get(key, {})
        logged = entry.get("football_minutes")
        scheduled = offset > 0 and day.weekday() in football_days
        if not scheduled and not recorded and logged is None:
            continue
        durations = [a["minutes"] for a in recorded if a["minutes"] is not None]
        recorded_minutes = round(sum(durations), 1) if durations else None
        minutes = logged if logged is not None else recorded_minutes
        rpe = entry.get("football_rpe") if minutes and minutes > 0 else None
        duration_complete = logged is not None or bool(recorded) and len(durations) == len(recorded)
        rows.append({"date": key, "scheduled": scheduled, "recording_count": len(recorded),
                     "recorded_minutes": recorded_minutes, "minutes": minutes, "rpe": rpe,
                     "source": "your log" if logged is not None else "Garmin" if recorded else "unobserved",
                     "status": "logged skipped" if logged == 0 else "logged" if logged is not None else "recorded" if recorded else "unobserved",
                     "duration_complete": duration_complete,
                     "conflict": logged == 0 and bool(recorded),
                     "session_rpe_load": round(minutes * rpe, 1) if minutes and rpe is not None and duration_complete else None})
    played = [r for r in rows if r["minutes"] is not None and r["minutes"] > 0]
    effort = [r for r in played if r["session_rpe_load"] is not None]
    return {"start": (today - timedelta(days=27)).isoformat(), "end": today.isoformat(), "days": rows,
            "scheduled_past_days": sum(r["scheduled"] for r in rows),
            "observed_playing_days": len(played), "known_minutes": round(sum(r["minutes"] for r in played), 1),
            "effort_logged_days": sum(r["rpe"] is not None for r in played), "complete_effort_days": len(effort),
            "session_rpe_load": round(sum(r["session_rpe_load"] for r in effort), 1) if effort else None,
            "missing_dates": [r["date"] for r in reversed(rows) if r["scheduled"] and r["status"] == "unobserved"],
            "incomplete_duration_days": sum(not r["duration_complete"] and r["status"] == "recorded" for r in rows),
            "note": "One row per date. Your duration log takes precedence over Garmin recordings on that date; neither is counted twice. Minutes are known totals, not a complete workload estimate. Session-RPE units cover only days with your effort rating and stay separate from Garmin load. Today's scheduled evening is not marked missing."}


def prescription_followthrough(store: Store, today: date, all_activities: list[dict]) -> dict:
    start, end = (today - timedelta(days=7)).isoformat(), today.isoformat()
    exists = store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='prescriptions'").fetchone()
    prescribed = [json.loads(row[0]) for row in store.db.execute(
        "SELECT payload FROM prescriptions WHERE date>=? AND date<?", (start, end))] if exists else []
    prescribed = [s for s in prescribed if s["kind"] in ("easy", "quality")]
    actual = [a for a in all_activities if a["kind"] == "run" and start <= a["date"] < end]
    planned_dates, actual_dates = {s["date"] for s in prescribed}, {a["date"] for a in actual}
    return {"start": start, "end": (today - timedelta(days=1)).isoformat(),
            "planned_days": len(planned_dates), "recorded_planned_days": len(planned_dates & actual_dates),
            "unrecorded_dates": sorted(planned_dates - actual_dates),
            "planned_km": round(sum(s.get("km") or 0 for s in prescribed), 2),
            "recorded_km": round(sum(a["km"] or 0 for a in actual), 2),
            "note": "Runs on prescribed dates support consistency, not proof of workout effort or step completion. Distance budgets may be maxima."}


def analyse(store: Store, today: date, preferences: dict | None = None) -> dict:
    preferences = preferences or {}
    football_days = preferences.get("football_days", [0, 6])
    football_names = ", ".join((date(2026, 10, 5) + timedelta(days=d)).strftime("%A") for d in football_days)
    all_activities, wellness = activities(store), daily(store)
    garmin_records = running_records(store.get("personal_records", "account"), today)
    garmin_5k = next((record for record in garmin_records if record["type_id"] == 3), None)
    recent = [a for a in all_activities if today - timedelta(days=27) <= date.fromisoformat(a["date"]) <= today]
    runs = [a for a in recent if a["kind"] == "run"]
    history_weeks = []
    for offset in reversed(range(26)):
        end = today - timedelta(days=7 * offset)
        start = end - timedelta(days=6)
        items = [a for a in all_activities if start.isoformat() <= a["date"] <= end.isoformat()]
        history_weeks.append({"start": start.isoformat(), "end": end.isoformat(),
                      "run_km": round(sum(a["km"] or 0 for a in items if a["kind"] == "run"), 2),
                      "run_count": sum(a["kind"] == "run" for a in items),
                      "running_days": len({a["date"] for a in items if a["kind"] == "run"}),
                      "football_count": sum(a["kind"] == "football" for a in items),
                      "recorded_minutes": round(sum(a["minutes"] or 0 for a in items), 1)})
    weeks = history_weeks[-8:]
    baseline = round(mean(w["run_km"] for w in weeks[-4:]), 2)
    # Duration covers pauses; elapsed duration is preferred for a defensible benchmark.
    candidates = []
    for a in all_activities:
        if a["kind"] != "run" or a["sport"] not in {"running", "track_running", "street_running"}:
            continue
        if not a["km"] or not 4.95 <= a["km"] <= 5.05:
            continue
        if not today - timedelta(days=90) <= date.fromisoformat(a["date"]) <= today:
            continue
        seconds = a["elapsed_seconds"] or a["duration_seconds"]
        if seconds and 600 <= seconds <= 3600:
            candidates.append({"date": a["date"], "seconds": round(seconds * 5 / a["km"]),
                               "activity_id": a["id"], "name": a["name"],
                               "basis": "Observed ~5 km outdoor activity; distance-adjusted elapsed time. Effort and course are unverified."})
    benchmark = min(candidates, key=lambda a: a["seconds"]) if candidates else None
    last_week = weeks[-1]["run_km"]
    previous_average = mean(w["run_km"] for w in weeks[-4:-1])
    change = round((last_week / previous_average - 1) * 100, 1) if previous_average > 0 else None
    followthrough = prescription_followthrough(store, today, all_activities)
    within_completed_budget = (followthrough["planned_days"] >= 2
        and followthrough["recorded_planned_days"] == followthrough["planned_days"]
        and followthrough["planned_km"] >= 5
        and 0 < followthrough["recorded_km"] <= followthrough["planned_km"] * 1.1
        and last_week <= followthrough["planned_km"] * 1.1)
    recent_wellness = [d for d in wellness if today - timedelta(days=6) <= date.fromisoformat(d["date"]) <= today]
    baseline_wellness = [d for d in wellness if today - timedelta(days=28) <= date.fromisoformat(d["date"]) < today - timedelta(days=2)]
    resting = [d["resting_hr"] for d in baseline_wellness if d["resting_hr"] and 25 <= d["resting_hr"] <= 110]
    baseline_hr = median(resting) if len(resting) >= 7 else None
    fresh = [d for d in wellness if today - timedelta(days=2) <= date.fromisoformat(d["date"]) <= today
             and (any(d[k] is not None for k in ("sleep_hours", "resting_hr", "hrv_ms", "readiness"))
                  or d["hrv_status"] in {"BALANCED", "LOW", "UNBALANCED", "POOR"})]
    flags = []
    if len([d for d in fresh if d["sleep_hours"] is not None and d["sleep_hours"] < 6]) >= 2:
        flags.append("Two recent nights below six hours sleep")
    if baseline_hr and any(d["resting_hr"] and d["resting_hr"] >= baseline_hr + 7 for d in fresh):
        flags.append("Recent resting heart rate at least 7 bpm above your personal baseline")
    if len([d for d in fresh if d["hrv_status"] in {"LOW", "UNBALANCED", "POOR"}]) >= 2:
        flags.append("Repeated low, unbalanced or poor Garmin HRV status")
    if any(d["readiness"] is not None and d["readiness"] < 30 for d in fresh):
        flags.append("Recent Garmin training readiness below 30")
    feedback = store.feedback()
    fresh_feedback = [v for k, v in feedback.items() if (today - timedelta(days=2)).isoformat() <= k <= today.isoformat()]
    previous_feedback = [v for k, v in feedback.items() if k <= today.isoformat()]
    latest_feedback = previous_feedback[-1] if previous_feedback else None
    # An unresolved pain report does not expire into permission to resume impact.
    pain = latest_feedback is not None and latest_feedback["pain"] > 0
    if pain:
        flags.append("Pain reported: pause impact training and seek assessment if persistent" if fresh_feedback else "Your last pain report needs an update before resuming impact training")
    elif latest_feedback and fresh_feedback and any(v["pain"] > 0 and v["date"] >= (today - timedelta(days=6)).isoformat() for v in previous_feedback):
        flags.append("Recent pain has settled in your latest check-in: return gradually with easy work")
    if fresh_feedback and (fresh_feedback[-1]["fatigue"] >= 7 or fresh_feedback[-1]["soreness"] >= 6):
        flags.append("High fatigue or soreness reported")
    if change is not None and change > 25 and not within_completed_budget:
        flags.append("Running distance increased more than 25% versus the preceding three weeks")
    insights = [
        {"title": "Build around your football", "detail": f"{football_names or 'No scheduled'} evening five-a-side. Count matches as demanding work; use the following day to reassess soreness. Occasional running quality is enabled only when your running base, session budget and recovery support it.", "evidence": "Your saved schedule; football intensity is assumed until recorded or logged."},
        {"title": "Your running baseline", "detail": f"{len(runs)} recorded runs and {sum(a['km'] or 0 for a in runs):.1f} km over 28 days; average {baseline:.1f} km/week.", "evidence": "Garmin activity history; zero recorded distance can also mean missing recordings."},
    ]
    previous_blocks = []
    for index in range(len(history_weeks) - 7):
        block = history_weeks[index:index + 4]
        count = sum(w["run_count"] for w in block)
        running_days = sum(w["running_days"] for w in block)
        if running_days >= 10:
            previous_blocks.append({"start": block[0]["start"], "end": block[-1]["end"], "runs": count,
                                    "running_days": running_days,
                                    "weekly_km": round(mean(w["run_km"] for w in block), 1)})
    previous_best = max(previous_blocks, key=lambda v: v["weekly_km"]) if previous_blocks else None
    if previous_best and len(runs) < 10 and previous_best["weekly_km"] > baseline * 1.5:
        insights.append({"title": "Rebuild the routine you have already shown",
                         "detail": f"From {previous_best['start']} to {previous_best['end']}, you recorded {previous_best['runs']} runs across {previous_best['running_days']} dates, averaging {previous_best['weekly_km']:.1f} km/week. Re-establish regular easy sessions before aiming to reproduce that volume or add harder running.",
                         "evidence": "An earlier four-week block within six months, with at least ten distinct running dates. Past volume does not establish current readiness."})
    if benchmark:
        insights.append({"title": "Current 5K reference", "detail": f"Observed 5K equivalent {format_time(benchmark['seconds'])} on {benchmark['date']}. Use this as a reference; a steady run does not establish race fitness.", "evidence": benchmark["basis"]})
    else:
        insights.append({"title": "Establish a 5K reference", "detail": "No suitable outdoor ~5 km activity in the last 90 days. Start with easy consistency; record a controlled 5K once you can comfortably run 30 minutes. Do not set race paces from age or height.", "evidence": "No qualifying activity; treadmill and trail runs excluded from the benchmark."})
    if garmin_5k:
        insights.append({"title": "Your Garmin 5K history", "detail": f"Garmin's accepted 5K record is {format_time(garmin_5k['seconds'])}" + (f" from {garmin_5k['date']}" if garmin_5k["date"] else " with an unknown date") + ". Treat this as historical context. An ordinary recent run is not a test of whether you can still reproduce that record; establish a fresh measured result after rebuilding consistency.", "evidence": garmin_5k["basis"]})
    if change is not None:
        insights.append({"title": "Running load trend", "detail": f"Latest seven days: {last_week:.1f} km, {change:+.0f}% versus the preceding three weeks. Adjust volume using how you feel and football load.", "evidence": "Rolling seven-day windows; this is a coaching heuristic, not an injury prediction."})
        if change > 25 and within_completed_budget:
            insights[-1]["detail"] += " Runs were recorded on all recent prescribed dates and distance remains within that budget. Earlier low-volume weeks inflate this percentage; it alone does not reduce the completed routine. Symptoms and recovery still govern the next session."
    sleep_mean = average(d["sleep_hours"] for d in recent_wellness)
    if sleep_mean is not None:
        insights.append({"title": "Recovery trend", "detail": f"Recorded sleep averaged {sleep_mean:.1f} hours over {sum(d['sleep_hours'] is not None for d in recent_wellness)} nights this week. Log soreness after both football matches; wearable scores alone cannot clear you for hard training.", "evidence": "Garmin sleep duration; missing nights excluded."})
    fitness_days = [d for d in wellness if d["vo2_max"] and (today - timedelta(days=90)).isoformat() <= d["date"] <= today.isoformat()]
    if fitness_days:
        latest_fitness = fitness_days[-1]
        insights.append({"title": "Aerobic fitness estimate", "detail": f"Garmin's latest running VO₂ max estimate is {latest_fitness['vo2_max']:.1f} on {latest_fitness['date']}. Track its trend alongside your running consistency and measured 5K, rather than using it to promise a race time.", "evidence": "Garmin generic running fitness metric; this is a device estimate, not a laboratory measurement."})
    training_days = [d for d in wellness if d["training_status"] and (today - timedelta(days=6)).isoformat() <= d["date"] <= today.isoformat()]
    if training_days:
        latest_training = training_days[-1]
        load_text = f" Acute load {latest_training['acute_load']:.0f} device units." if latest_training["acute_load"] is not None else ""
        insights.append({"title": "Garmin's training context", "detail": f"Garmin reported {latest_training['training_status'].lower()} on {latest_training['date']}." + load_text + " Compare this with actual running, football and how you feel; the device label does not clear a hard session or predict injury.", "evidence": "Matching-date primary-device training status and load. Device load remains separate from your session-RPE logs."})
    pacing = []
    splits = store.records("splits")
    for a in runs:
        raw_splits = splits.get(a["id"]) or {}
        laps = raw_splits.get("lapDTOs", []) if isinstance(raw_splits, dict) else []
        laps = laps if isinstance(laps, list) else []
        kilometre_laps = [lap for lap in laps if isinstance(lap, dict) and number(lap.get("distance")) and 900 <= float(lap["distance"]) <= 1100 and number(lap.get("elapsedDuration") or lap.get("duration"))]
        if len(kilometre_laps) >= 4:
            midpoint = len(kilometre_laps) // 2
            def lap_pace(group):
                return sum(float(lap.get("elapsedDuration") or lap["duration"]) for lap in group) / (sum(float(lap["distance"]) for lap in group) / 1000)
            change_seconds = round(lap_pace(kilometre_laps[midpoint:]) - lap_pace(kilometre_laps[:midpoint]), 1)
            pacing.append({"activity_id": a["id"], "date": a["date"], "second_half_pace_change_seconds_km": change_seconds, "laps": len(kilometre_laps)})
    if pacing:
        newest = pacing[0]
        insights.append({"title": "Your latest split pattern", "detail": f"On {newest['date']}, the latter kilometre laps were {newest['second_half_pace_change_seconds_km']:+.0f} sec/km slower than the earlier laps. Practise an even, controlled start; hills, stops and workout structure can also explain the pattern.", "evidence": f"{newest['laps']} laps of 900–1100 m; elapsed lap time preferred. This describes pace, not fatigue causation."})
    if flags:
        insights.append({"title": "Ease the next sessions", "detail": "; ".join(flags) + ". Reassess daily; the next three days of running are reduced or paused.", "evidence": "Transparent coaching thresholds using fresh observations, not a medical diagnosis."})
    football = football_summary(all_activities, feedback, today, football_days)
    football_missing = football["missing_dates"]
    if football_missing:
        insights.append({"title": "Football load is partly unobserved", "detail": f"{len(football_missing)} scheduled match dates have no football recording or duration log. Log minutes and effort after matches so recovery decisions include them.", "evidence": "Schedule expectation; unrecorded matches are not treated as confirmed activity."})
    if football["observed_playing_days"]:
        insights.append({"title": "Your football alongside running", "detail": f"{football['observed_playing_days']} playing days with {football['known_minutes']:.0f} known minutes in 28 days. Effort is logged on {football['effort_logged_days']} of those days. Keep running easy around demanding matches and use your next check-in to report how your legs recovered.", "evidence": football["note"]})
    if runs:
        largest = max(runs, key=lambda a: a["km"] or 0)
        total_km = sum(a["km"] or 0 for a in runs)
        if total_km and (largest["km"] or 0) / total_km > .45 and total_km > 10:
            insights.append({"title": "Spread your running across the week", "detail": f"Your longest recent run ({largest['km']:.1f} km on {largest['date']}) accounts for {(largest['km'] or 0) / total_km:.0%} of the last 28 days' running. Shorter, more regular easy runs give you more frequent practice while leaving room to recover from football.", "evidence": "Distribution of recorded running distance; no claim that a specific distribution prevents injury."})
    strength_sessions = [a for a in recent if a["sport"] == "strength_training"]
    if preferences.get("gym", True):
        insights.append({"title": "Use the gym to support running", "detail": f"{len(strength_sessions)} strength sessions recorded in 28 days. Use the plan's short strength session after easy running; keep optional work light enough to recover before football. Build controlled squat/hinge, single-leg and calf work gradually.", "evidence": "Recorded strength activities and your gym access; exercise weights and unrecorded sessions are unknown."})
    logged_load = [{"date": day["date"], "minutes": day["minutes"], "rpe": day["rpe"],
                    "duration_source": day["source"], "session_rpe_load": day["session_rpe_load"]}
                   for day in football["days"] if day["session_rpe_load"] is not None]
    if logged_load:
        insights.append({"title": "Your logged football effort", "detail": f"Effort rated on {len(logged_load)} playing days covering {sum(x['minutes'] for x in logged_load):.0f} minutes in 28 days. Duration × perceived effort totals {sum(x['session_rpe_load'] for x in logged_load):.0f} arbitrary units for that subset. Keep this distinct from Garmin's device training load.", "evidence": "Your effort ratings paired with your duration log or Garmin duration on the same date. Days without a rating are excluded; duration is not counted twice."})
    # Comparable HR/pace is descriptive; exclude hills, short runs and non-outdoor running.
    comparable = [a for a in runs if a["sport"] == "running" and a["km"] and a["km"] >= 3 and a["average_hr"] and a["pace_seconds_km"] and a["ascent_m"] is not None and a["ascent_m"] / a["km"] < 10]
    early = [a for a in comparable if a["date"] < (today - timedelta(days=13)).isoformat()]
    late = [a for a in comparable if a["date"] >= (today - timedelta(days=13)).isoformat()]
    efficiency = None
    if len(early) >= 3 and len(late) >= 3 and abs(mean(a["average_hr"] for a in early) - mean(a["average_hr"] for a in late)) <= 5:
        efficiency = round(mean(a["pace_seconds_km"] for a in early) - mean(a["pace_seconds_km"] for a in late), 1)
        insights.append({"title": "Pace at similar heart rate", "detail": f"Recent comparable runs averaged {efficiency:+.1f} seconds/km faster than the earlier fortnight. Weather, route and effort can explain differences.", "evidence": f"{len(early)} earlier and {len(late)} recent outdoor runs; mean heart rates within 5 bpm; climbing below 10 m/km."})
    coverage = {}
    for key in ("sleep_hours", "resting_hr", "hrv_ms", "hrv_status", "readiness", "body_battery_last", "vo2_max", "acute_load", "training_status", "stress"):
        valid = [d for d in wellness if d["date"] <= today.isoformat() and d[key] is not None]
        latest = valid[-1]["date"] if valid else None
        coverage[key] = {"recorded_days": len(valid), "latest_date": latest,
                         "age_days": (today - date.fromisoformat(latest)).days if latest else None,
                         "recent_7_days": sum(d[key] is not None for d in recent_wellness)}
    observed_recovery = [d for d in wellness if d["date"] <= today.isoformat()
                         and (any(d[k] is not None for k in ("sleep_hours", "resting_hr", "hrv_ms", "readiness"))
                              or d["hrv_status"] in {"BALANCED", "LOW", "UNBALANCED", "POOR"})]
    latest_recovery = observed_recovery[-1]["date"] if observed_recovery else None
    if not fresh:
        detail = f"Latest usable recovery reading: {latest_recovery}." if latest_recovery else "No usable recovery readings have been imported."
        insights.insert(2, {"title": "Bring your recovery picture up to date",
                            "detail": detail + " Sync your watch with Garmin Connect, refresh here, and log how your legs feel. Easy sessions remain guided by comfort; stale readings do not clear you for hard running.",
                            "evidence": "No usable sleep, resting HR, HRV or readiness in the last three calendar days."})
    return {"activities": all_activities, "daily": wellness, "weeks": weeks, "history_weeks": history_weeks,
            "garmin_records": garmin_records, "garmin_5k_record": garmin_5k,
            "earlier_consistent_block": previous_best,
            "baseline_weekly_km": baseline, "recent_run_count": len(runs),
            "recent_running_days": len({a["date"] for a in runs}),
            "recent_prescription_followthrough": followthrough,
            "volume_change_within_completed_budget": within_completed_budget,
            "benchmark": benchmark, "weekly_change_percent": change,
            "sleep_average_hours": sleep_mean, "resting_hr_baseline": baseline_hr,
            "recovery": {"status": "pause" if pain else "reduce" if flags else "unknown" if not fresh and not fresh_feedback else "normal",
                         "flags": flags, "fresh_days": len(fresh), "latest_checkin_date": latest_feedback["date"] if latest_feedback else None},
            "insights": insights, "unobserved_football_dates": football_missing,
            "feedback": list(feedback.values()), "football_logged_load": logged_load, "pacing": pacing,
            "football_summary": football,
            "data_quality": {"metrics": coverage, "latest_activity_date": all_activities[0]["date"] if all_activities else None,
                             "latest_recovery_date": latest_recovery,
                             "mismatched_daily_dates": [{"date": d["date"], "streams": d["date_mismatches"]} for d in wellness if d["date_mismatches"]],
                             "note": "Missing, non-finite and out-of-range sensor values are excluded from coaching. Requested and provider dates must agree when a provider date is present."},
            "efficiency_change_seconds_km": efficiency}


def format_time(seconds: float) -> str:
    value = round(seconds)
    return f"{value // 60}:{value % 60:02d}"
