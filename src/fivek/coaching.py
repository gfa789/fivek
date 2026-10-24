"""Validated athlete choices kept separate from Garmin observations."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from .config import Config


def validate_preferences(body: dict, today: date) -> dict:
    allowed = {"target_date", "running_days", "football_days", "gym"}
    if set(body) - allowed:
        raise ValueError("Unknown training preference")
    result = {}
    if "target_date" in body:
        try:
            target = date.fromisoformat(body["target_date"])
        except (TypeError, ValueError):
            raise ValueError("Choose a valid goal date") from None
        if not today <= target <= today + timedelta(days=730):
            raise ValueError("Goal date must be today or within the next two years")
        result["target_date"] = target.isoformat()
    for name in ("running_days", "football_days"):
        if name in body:
            values = body[name]
            if not isinstance(values, list) or any(type(v) is not int or not 0 <= v <= 6 for v in values):
                raise ValueError(f"{name} must contain weekdays from 0 (Monday) to 6 (Sunday)")
            if len(values) != len(set(values)) or len(values) > (3 if name == "running_days" else 2):
                raise ValueError("Choose up to three running days and two football days without duplicates")
            if name == "running_days" and not values:
                raise ValueError("Choose at least one running day")
            result[name] = sorted(values)
    if "gym" in body:
        if type(body["gym"]) is not bool:
            raise ValueError("gym must be a boolean")
        result["gym"] = body["gym"]
    running = result.get("running_days", [2, 3, 5])
    football = result.get("football_days", [0, 6])
    if set(running) & set(football):
        raise ValueError("Choose running days that do not overlap football")
    return result


def effective_config(config: Config, preferences: dict) -> Config:
    return replace(config, target=date.fromisoformat(preferences.get("target_date", config.target.isoformat())))


def validate_benchmark(body: dict, today: date) -> dict | None:
    if body.get("clear") is True:
        return None
    try:
        day = date.fromisoformat(body["date"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Choose the date of your measured 5K") from None
    if not today - timedelta(days=365) <= day <= today:
        raise ValueError("Result must be from the last year and cannot be in the future")
    seconds = body.get("seconds")
    if type(seconds) is not int or not 600 <= seconds <= 7200:
        raise ValueError("Enter a 5K result between 10 minutes and 2 hours")
    kind = body.get("kind")
    if kind not in {"race", "time_trial", "controlled"}:
        raise ValueError("Choose race, time trial or controlled effort")
    if body.get("measured_course") is not True:
        raise ValueError("Confirm that you covered a measured 5K course using elapsed time")
    note = body.get("note", "")
    if not isinstance(note, str) or len(note) > 500:
        raise ValueError("Result note must contain at most 500 characters")
    return {"date": day.isoformat(), "seconds": seconds, "kind": kind,
            "measured_course": True, "note": note, "origin": "athlete",
            "basis": "Athlete-confirmed measured 5K with elapsed time; conditions are self-reported."}
