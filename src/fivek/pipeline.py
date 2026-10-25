from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from garmin_api import GarminAPI
from .analysis import analyse
from .config import Config, local_today
from .coaching import effective_config
from .plan import make_plan
from .store import Store, now, refresh_lock, report_lock
from .sync import Sync, SyncCancelled
from .resources import expected_resources


SOURCES = [
    {"title": "Garmin Connect adapter and data availability", "url": "https://github.com/cyberjunky/python-garminconnect"},
    {"title": "Boston Athletic Association 5K training plans", "url": "https://www.baa.org/races/boston-5k/info-for-athletes/boston-5k-training/"},
    {"title": "Garmin's explanation of HRV status", "url": "https://www.garmin.com/en-IE/garmin-technology/health-science/hrv-status/"},
    {"title": "Garmin's personal record criteria", "url": "https://support.garmin.com/en-GB/?faq=GePPQ3FJYO0A8TAHLeC7CA"},
    {"title": "NHS guidance on running pain and injuries", "url": "https://www.nhs.uk/live-well/exercise/knee-pain-and-other-running-injuries/"},
    {"title": "AIS fuelling and energy availability", "url": "https://www.ausport.gov.au/ais/nutrition/energy-availability"},
    {"title": "Garmin performance estimates", "url": "https://www8.garmin.com/manuals/webhelp/GUID-D3C2D1F9-D2C0-404D-9372-7B2D57459BF8/EN-US/GUID-F5BF67CE-C94E-4842-AE96-A7A05C85B732.html"},
]
PROCESSING_VERSION = 18


def atomic_text(path: Path, content: str):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def session_description(session: dict) -> str:
    lines = [session["details"]]
    if session.get("readiness_note"):
        lines.append(session["readiness_note"])
    if session.get("km"):
        qualifier = "maximum distance" if session["kind"] == "quality" else "distance"
        lines.append(f"{qualifier.capitalize()}: {session['km']} km.")
    if session.get("minutes") or session.get("estimated_minutes"):
        lines.append(f"Duration: {session.get('minutes') or session['estimated_minutes']} minutes.")
    lines.append("Effort: " + session["effort"] + ".")
    if session.get("optional"):
        lines.append("Optional session.")
    if session.get("conditional"):
        lines.append("Forecast: refresh before this week; adjust to actual training and recovery.")
    if session.get("status") in ("recorded", "skipped"):
        lines.append("Status: " + session["status"] + ".")
    if session.get("next_action"):
        lines.append(session["next_action"])
    if session.get("decision_reasons"):
        lines.append("Why this session: " + " ".join(session["decision_reasons"]))
    if session.get('gym'):
        gym=session['gym']
        lines.append(gym['name']+'. '+gym['effort'])
        lines.extend(f"{e['name']}: {e['sets']} sets x {e['reps']}; rest {e['rest_seconds']} sec. {e['cue']}" for e in gym['exercises'])
        lines.extend([gym['progression'],gym['adjustment']])
    if session.get('fuel'):
        lines.append(session['fuel'])
    if session.get('total_minutes'):
        lines.append(f"Combined session time: approximately {session['total_minutes']} minutes.")
    return "\n".join(lines)


def calendar(plan: dict) -> str:
    def escape(value):
        return str(value).replace("\r\n", "\n").replace("\r", "\n").replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//FiveK//Training Plan//EN", "CALSCALE:GREGORIAN"]
    generated = datetime.fromisoformat(plan.get("generated_at") or now())
    stamp = generated.replace(tzinfo=generated.tzinfo or timezone.utc).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for session in plan["sessions"]:
        day = date.fromisoformat(session["date"])
        title = session["title"]
        if session.get("status") in ("recorded", "skipped"):
            title += " · " + session["status"].capitalize()
        lines.extend(["BEGIN:VEVENT", f"UID:fivek-{day.isoformat()}@local", f"DTSTAMP:{stamp}",
                      f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
                      f"DTEND;VALUE=DATE:{(day + timedelta(days=1)).strftime('%Y%m%d')}",
                      "SUMMARY:" + escape(title),
                      "DESCRIPTION:" + escape(session_description(session)), "END:VEVENT"])
    lines.append("END:VCALENDAR")
    # RFC 5545 folds at 75 octets without breaking UTF-8 characters.
    folded = []
    for line in lines:
        part = ""
        for char in line:
            if len((part + char).encode("utf-8")) > 75:
                folded.append(part)
                part = " "
            part += char
        folded.append(part)
    return "\r\n".join(folded) + "\r\n"


def report(store: Store, config: Config, today: date | None = None) -> dict:
    with report_lock(config.data_dir):
        return _report(store, config, today)


def _report(store: Store, config: Config, today: date | None = None) -> dict:
    today = today or local_today()
    configuration = config.report_settings()
    existing = store.latest_report()
    if existing and existing.get("processing_version", 0) > PROCESSING_VERSION:
        raise RuntimeError("A newer FiveK version created the current report. Restart using that version before processing this data.")
    if store.setting("program_start") is None:
        store.set_setting("program_start", today.isoformat())
    # Keep a durable daily record of prescriptions. Past dates are never rewritten.
    store.db.execute("CREATE TABLE IF NOT EXISTS prescriptions(date TEXT PRIMARY KEY,payload TEXT NOT NULL)")
    store.db.commit()
    with store.snapshot():
        preferences = store.setting("preferences", {}) or {}
        metrics = analyse(store, today, preferences)
        metrics["program_start"] = store.setting("program_start")
        metrics["preferences"] = preferences
        confirmed = store.setting("benchmark")
        metrics["observed_benchmark"] = metrics["benchmark"]
        metrics["confirmed_benchmark"] = confirmed
        metrics["benchmark_history"] = store.setting("benchmark_history", [])
        if confirmed and 0 <= (today - date.fromisoformat(confirmed["date"])).days <= 90:
            metrics["benchmark"] = confirmed
            metrics["insights"] = [v for v in metrics["insights"] if v["title"] not in {"Current 5K reference", "Establish a 5K reference"}]
            from .analysis import format_time
            metrics["insights"].insert(2, {"title": "Your confirmed 5K result", "detail": f"{format_time(confirmed['seconds'])} on {confirmed['date']} ({confirmed['kind'].replace('_', ' ')}). Use a recent result and current recovery to choose your next goal.", "evidence": confirmed["basis"]})
        historic = [json.loads(r[0]) for r in store.db.execute("SELECT payload FROM prescriptions WHERE date<?", (today.isoformat(),))]
        sync_status = store.sync_status()
        activity_ids = store.get("activity_roster", "current")
        if activity_ids is None:
            activity_ids = [r[0] for r in store.db.execute("SELECT key FROM raw WHERE stream='activities'")]
        history_start = config.start or (min([today] + [date.fromisoformat(a["date"]) for a in metrics["activities"]]) if activity_ids else today - timedelta(days=365))
        sync_status["scope"] = {"history_start": history_start.isoformat(), "history_end": today.isoformat(),
                                **store.resource_coverage(expected_resources(history_start, today, activity_ids))}
        source_revision = store.setting("data_revision", 0)
        source_watermark = dict(store.db.execute("SELECT COUNT(*) records,MAX(fetched_at) latest_fetch FROM raw").fetchone())
        from .physiology import athlete_insights
        metrics["athlete"] = athlete_insights(store, metrics, today, effective_config(config, metrics["preferences"]).target)
    config = effective_config(config, metrics["preferences"])
    plan = make_plan(metrics, config, today)
    plan["generated_at"] = now()
    unrecorded = metrics["recent_prescription_followthrough"]["unrecorded_dates"]
    if unrecorded:
        metrics["insights"].insert(2, {"title": "Leave unrecorded sessions behind",
                                     "detail": f"No run is recorded on {len(unrecorded)} prescribed dates in the past week: {', '.join(unrecorded)}. Sync the watch if recordings are pending. The current plan leaves those days behind and caps each upcoming session; do not add their distance to another run.",
                                     "evidence": "Past stored prescriptions compared with local Garmin running dates. An absent recording does not prove that you skipped the session."})
    if plan.get("volume_basis", {}).get("held_completed_routine"):
        metrics["insights"].insert(2, {"title": "Keep the routine you have completed",
                                     "detail": f"Runs were recorded on every prescribed running date in the past week. The plan uses {plan['volume_basis']['starting_weekly_km']:.1f} km as its starting routine even though the four-week average is lower. Continue easy consistency and reassess recovery before progressing.",
                                     "evidence": metrics["recent_prescription_followthrough"]["note"] + " No hold is earned by missed dates or excess unplanned mileage."})
    planned_runs = [s for s in historic if s["kind"] in ("easy", "quality", "race") and (today - date.fromisoformat(s["date"])).days <= 28]
    completed = sum(any(a["kind"] == "run" and a["date"] == s["date"] for a in metrics["activities"]) for s in planned_runs)
    metrics["adherence"] = {"planned_running_days": len(planned_runs), "recorded_running_days": completed,
                            "note": "Presence of a run on a prescribed date; this does not prove workout completion. History begins when this pipeline first plans sessions."}
    if planned_runs:
        metrics["insights"].append({"title": "How the plan is landing",
                                    "detail": f"Runs recorded on {completed} of {len(planned_runs)} prescribed running days in the last 28 days. If sessions are being missed, keep the next week achievable rather than catching up all at once.",
                                    "evidence": metrics["adherence"]["note"]})
    store.db.execute("DELETE FROM prescriptions WHERE date>=?", (today.isoformat(),))
    for session in plan["sessions"]:
        store.db.execute("INSERT INTO prescriptions VALUES (?,?)", (session["date"], json.dumps(session)))
    result = {"schema_version": 2, "generated_at": plan["generated_at"], "as_of": today.isoformat(),
              "processing_version": PROCESSING_VERSION,
              "configuration": configuration,
              "source_revision": source_revision,
              "source_watermark": source_watermark,
              "profile": {"age": config.age, "sex": config.sex, "height_cm": config.height_cm,
                          "football_days": metrics["preferences"].get("football_days", [0, 6]),
                          "football": "Evening five-a-side", "gym": metrics["preferences"].get("gym", True)},
              "metrics": metrics, "plan": plan, "sync": sync_status, "sources": SOURCES,
              "limitations": ["Optimises training decisions toward a faster 5K; no model can guarantee your optimal time.",
                              "Garmin endpoints are unofficial and some metrics depend on device or account support.",
                              "Activity charts may be sampled by Garmin; stored responses are not original FIT files.",
                              "Wellness starts at your oldest recorded activity by default; set FIVEK_HISTORY_START for earlier wellness history.",
                              "Plan progression and recovery thresholds are explicit coaching heuristics; refresh and log how you feel."]}
    store.save_report(result)
    atomic_text(config.data_dir / "report.json", json.dumps(result, indent=2, allow_nan=False))
    atomic_text(config.data_dir / "training-plan.ics", calendar(plan))
    atomic_text(config.data_dir / "insights.md", markdown(result))
    return result


def markdown(result: dict) -> str:
    plan, metrics = result["plan"], result["metrics"]
    text = [f"# Your 5K plan · {result['as_of']}", "", f"Target: {plan['target_date']}. {plan['days_remaining']} days remaining.", "", plan["strategy"], "", plan["pace_guidance"], ""]
    if plan.get("progression"):
        review = plan["progression"]
        text.extend(["## Your next step", "", review["title"], "", review["action"], ""])
        for item in review["milestones"]:
            text.extend([f"### {item['title']} · {item['status']}", "", item["detail"], "", item["evidence"], ""])
        text.extend([f"Taper starts {review['taper_start']}. {review['note']}", ""])
    for insight in metrics["insights"]:
        text.extend([f"## {insight['title']}", "", insight["detail"], "", "Evidence: " + insight["evidence"], ""])
    text.extend(["## Next seven days", ""])
    for session in plan["sessions"][:7]:
        text.extend([f"### {session['date']} · {session['title']}", "", session_description(session), ""])
    text.extend(["## Weekly roadmap", "", "Later weeks are forecasts: refresh as recorded training and recovery change.", ""])
    for week in plan["weeks"]:
        volume = f"{week['planned_training_km']} km training" if week['planned_training_km'] else ""
        if week.get("planned_training_minutes"):
            volume += (" + " if volume else "") + f"{week['planned_training_minutes']} minutes easy/run-walk"
        if week.get("goal_run_km"):
            volume += (" + " if volume else "") + f"{week['goal_run_km']} km goal effort"
        text.append(f"- {week['start']} · {week['phase']} · remaining running: {volume or 'none'}.")
    text.extend(["", "## Sources and assumptions", ""])
    text.extend(f"- [{source['title']}]({source['url']})" for source in result["sources"])
    text.extend(f"- {item}" for item in result["limitations"])
    return "\n".join(text) + "\n"


def refresh(api: GarminAPI, config: Config, *, full=False, progress=print, cancel=None) -> dict:
    with refresh_lock(config.data_dir):
        store = Store(config.data_dir)
        run = store.start_run()
        coordinator = Sync(api, store, config, progress, publish=lambda: report(store, config), cancel=cancel)
        try:
            summary = coordinator.run(run, full=full)
            status = ("partial" if summary["errors"] or summary["coverage"]["error"] else
                      "backfilling" if summary["coverage"]["missing"] else "complete")
            store.finish(run, status, json.dumps(summary))
            return report(store, config)
        except SyncCancelled as exc:
            store.progress(run, "Cancelling", coordinator.telemetry())
            store.finish(run, "cancelled", str(exc))
            report(store, config)
            raise
        except Exception as exc:
            # Provider errors have safe messages; unexpected errors never expose payloads.
            from garmin_api import GarminError
            message = str(exc) if isinstance(exc, (GarminError, ValueError)) else "Refresh interrupted. Saved data can be resumed on the next refresh."
            store.progress(run, "Stopping", coordinator.telemetry())
            store.finish(run, "partial", message)
            report(store, config)
            raise
        except KeyboardInterrupt:
            store.progress(run, "Interrupting", coordinator.telemetry())
            store.finish(run, "interrupted", "Interrupted; saved checkpoints will resume.")
            report(store, config)
            raise
        finally:
            store.close()
