"""Read resources, accepted response containers and expected import scope."""
from __future__ import annotations

from datetime import date, timedelta

from garmin_api import GarminConnectionError


DAILY = {
    "daily": "get_stats", "sleep": "get_sleep_data", "hrv": "get_hrv_data",
    "readiness": "get_training_readiness", "training": "get_training_status",
    "fitness": "get_max_metrics", "body_battery": "get_body_battery",
    "stress": "get_stress_data", "heart_rate": "get_heart_rates",
    "respiration": "get_respiration_data", "spo2": "get_spo2_data",
}
ACTIVITY = {"activity": "get_activity", "splits": "get_activity_splits", "samples": "get_activity_details"}
ACCOUNT = {"profile": "get_user_profile", "devices": "get_devices", "personal_records": "get_personal_record"}
RANGED = {"body_composition": "get_body_composition", "race_predictions": "get_race_predictions"}


def validate_payload(stream: str, payload) -> None:
    # Empty containers vary across adapter versions; preserve genuine empties.
    if payload is None or payload == {} or payload == []:
        return
    arrays = {"devices", "readiness", "fitness", "body_battery", "personal_records", "race_predictions"}
    valid = isinstance(payload, dict) and stream != "devices"
    if stream in arrays and isinstance(payload, list):
        valid = all(isinstance(item, dict) for item in payload)
    if not valid:
        raise GarminConnectionError("Garmin returned an unexpected data format for " + stream + "; the last good source response is retained.")


def expected_resources(start: date, end: date, activity_ids) -> dict[str, list[str]]:
    days = [(start + timedelta(days=offset)).isoformat() for offset in range(max(0, (end - start).days + 1))]
    keys = sorted(set(str(value) for value in activity_ids))
    return {**{stream: days for stream in DAILY}, **{stream: keys for stream in ACTIVITY},
            "activities": keys, "activity_roster": ["current"], **{stream: ["account"] for stream in ACCOUNT},
            **{stream: ["recent"] for stream in RANGED}}
