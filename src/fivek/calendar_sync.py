"""Owned, rolling-week calendar events and Windows-account protected credentials."""
from __future__ import annotations

import ctypes
import json
import os
import uuid
from datetime import date, timedelta
from .pipeline import calendar
from .store import Store, file_lock, now


class CalendarError(RuntimeError):
    pass


def protect(data: bytes, *, decrypt=False) -> bytes:
    if os.name != "nt":
        from cryptography.fernet import Fernet, InvalidToken
        key_path = os.getenv("FIVEK_CALENDAR_KEY_FILE")
        if not key_path:
            raise CalendarError("Calendar encryption key is not configured on this server.")
        from pathlib import Path
        try:
            key = Path(key_path)
            if key.stat().st_mode & 0o077:
                raise CalendarError("Calendar encryption key must be private (mode 0600).")
            cipher = Fernet(key.read_bytes().strip())
            return cipher.decrypt(data) if decrypt else cipher.encrypt(data)
        except (OSError, ValueError, InvalidToken):
            raise CalendarError("Calendar credentials could not be opened. Reconnect on this server.") from None
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    method = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    method.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    method.restype = wintypes.BOOL
    if not method(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise CalendarError("Calendar credentials could not be opened for this Windows account. Reconnect locally.")
    kernel = ctypes.WinDLL("kernel32")
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def credentials(config) -> dict | None:
    path = config.data_dir / "calendar-credentials.bin"
    return json.loads(protect(path.read_bytes(), decrypt=True)) if path.exists() else None


def save_credentials(config, values):
    path = config.data_dir / "calendar-credentials.bin"
    temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(protect(json.dumps(values).encode()))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def connect_apple(config, body):
    username, password = body.get("username"), body.get("password")
    if not isinstance(username, str) or not 3 <= len(username.strip()) <= 254:
        raise ValueError("Enter your Apple Account email address")
    if not isinstance(password, str) or not 8 <= len(password.strip()) <= 128:
        raise ValueError("Enter an Apple app-specific password, not your main password")
    values = {"provider": "apple", "username": username.strip(), "password": password.strip()}
    import caldav
    try:
        with caldav.DAVClient(url="https://caldav.icloud.com", username=values["username"],
                              password=values["password"], timeout=20) as client:
            principal = client.principal()
            calendars = [c for c in principal.calendars() if c.get_display_name() == "FiveK Training"]
            if len(calendars) > 1:
                raise CalendarError("More than one FiveK Training calendar exists. Rename the duplicate before connecting.")
            selected = calendars[0] if calendars else principal.make_calendar(name="FiveK Training")
            values["calendar_url"] = str(selected.url)
        previous = credentials(config)
        save_credentials(config, values)
        if previous and (previous["username"], previous["calendar_url"]) != (values["username"], values["calendar_url"]):
            store = Store(config.data_dir)
            try:
                store.set_settings({"calendar_namespace": uuid.uuid4().hex, "calendar_events": {}, "calendar_sync": None})
            finally:
                store.close()
        return {"message": "Apple Calendar connected to FiveK Training", "connected": True}
    except CalendarError:
        raise
    except Exception:
        raise CalendarError("Apple Calendar could not connect. Check your account email, app-specific password and network access.") from None


class AppleCalendar:
    def __init__(self, values):
        import caldav
        self.client = caldav.DAVClient(url="https://caldav.icloud.com", username=values["username"],
                                       password=values["password"], timeout=20)
        self.calendar = caldav.Calendar(client=self.client, url=values["calendar_url"])

    def close(self):
        self.client.close()

    def get(self, uid):
        import caldav
        from urllib.parse import quote
        from caldav.lib.error import NotFoundError
        try:
            # iCloud may reject UID-filter REPORTs on an empty calendar. Our
            # deterministic resource URL needs only an authenticated GET.
            href = self.calendar.url.join(quote(uid, safe="") + ".ics")
            event = caldav.Event(client=self.client, parent=self.calendar, url=href)
            event.load(multiget_fallback=False)
            return event
        except NotFoundError:
            return None

    def put(self, uid, data, existing=None):
        if existing:
            existing.data = data
            existing.save()
        else:
            self.calendar.save_event(data)

    def delete(self, existing):
        existing.delete()


def desired_sessions(result):
    today = date.fromisoformat(result["as_of"])
    end = (today + timedelta(days=7)).isoformat()
    selected = []
    for session in result["plan"]["sessions"]:
        if not result["as_of"] <= session["date"] < end:
            continue
        if session["kind"] in {"easy", "quality", "race"}:
            selected.append(session)
        elif session.get("status") == "recorded" and session.get("recorded_activity_ids"):
            selected.append({**session, "title": "Run recorded · Recover", "kind": "easy",
                             "details": session.get("next_action") or session["details"],
                             "km": session.get("actual_run_km"), "minutes": None})
    return selected


def event_data(session, namespace, generated):
    text = calendar({"sessions": [session], "generated_at": generated})
    text = text.replace("UID:fivek-", "UID:fivek-" + namespace + "-")
    return text.replace("END:VEVENT", "TRANSP:TRANSPARENT\r\nX-FIVEK-MANAGED:TRUE\r\nX-FIVEK-NAMESPACE:" + namespace + "\r\nEND:VEVENT")


def semantic_event(data):
    from icalendar import Calendar
    parsed = Calendar.from_ical(data)
    event = next(iter(parsed.walk("VEVENT")), None)
    if event is None:
        raise CalendarError("The managed calendar event is malformed; it was not changed.")
    return {key: str(event.get(key, "")) for key in ("UID", "DTSTART", "DTEND", "SUMMARY", "DESCRIPTION", "TRANSP", "X-FIVEK-MANAGED", "X-FIVEK-NAMESPACE")}


def sync_calendar(config, result, provider=None):
    with file_lock(config.data_dir, "calendar.lock", "A calendar sync is already running.", wait_seconds=20):
        store = Store(config.data_dir)
        owned_provider = provider is None
        try:
            latest = store.latest_report()
            if latest and "source_revision" in result and latest.get("source_revision", -1) > result["source_revision"]:
                result = latest
            if provider is None:
                values = credentials(config)
                if values is None:
                    return {"connected": False, "message": "Connect Apple Calendar to publish the coming week's runs"}
                provider = AppleCalendar(values)
            namespace = store.setting("calendar_namespace") or uuid.uuid4().hex
            store.set_setting("calendar_namespace", namespace)
            registry = store.setting("calendar_events", {})
            counts = {"created": 0, "updated": 0, "deleted": 0, "unchanged": 0}
            sessions = {s["date"]: s for s in desired_sessions(result)}
            # Preserve owned history and label unrecorded days without inventing a missed workout.
            for day in registry:
                if day >= result["as_of"] or day < (date.fromisoformat(result["as_of"]) - timedelta(days=7)).isoformat():
                    continue
                prior = dict(registry[day]["session"])
                recorded = [a for a in result["metrics"]["activities"] if a["kind"] == "run" and a["date"] == day]
                prior["status"] = "recorded" if recorded else "planned"
                prior["title"] = registry[day]["session"]["title"] + ("" if recorded else " · No run recorded")
                prior["next_action"] = "Run recorded; recover and follow the updated plan." if recorded else "No run is recorded for this date. A watch sync may still be pending. Do not catch up this session; follow the refreshed plan."
                sessions[day] = prior
            for day in sorted(set(sessions) | {d for d in registry if d >= result["as_of"]}):
                uid = "fivek-" + namespace + "-" + day + "@local"
                remote = provider.get(uid)
                if remote is not None:
                    actual = semantic_event(remote.data)
                    if actual["X-FIVEK-MANAGED"] != "TRUE" or actual["X-FIVEK-NAMESPACE"] != namespace:
                        raise CalendarError("An event does not match FiveK ownership; it was not changed.")
                if remote is None and day < result["as_of"]:
                    registry.pop(day, None)
                    store.set_setting("calendar_events", registry)
                    continue
                if day not in sessions:
                    if remote is not None:
                        provider.delete(remote)
                        counts["deleted"] += 1
                    registry.pop(day, None)
                else:
                    session = sessions[day]
                    data = event_data(session, namespace, result["generated_at"])
                    if remote is None:
                        provider.put(uid, data)
                        counts["created"] += 1
                    elif actual != semantic_event(data):
                        provider.put(uid, data, remote)
                        counts["updated"] += 1
                    else:
                        counts["unchanged"] += 1
                    # Store original prescription so history labels do not accumulate.
                    original = registry.get(day, {}).get("session") if day < result["as_of"] else session
                    registry[day] = {"uid": uid, "session": original or session}
                store.set_setting("calendar_events", registry)
            status = {"connected": True, "calendar": "FiveK Training", "synced_at": now(),
                      "window_start": result["as_of"], "window_end": (date.fromisoformat(result["as_of"]) + timedelta(days=6)).isoformat(), **counts}
            store.set_setting("calendar_sync", status)
            store.set_setting("calendar_sync_error", None)
            return status
        except CalendarError:
            raise
        except Exception:
            raise CalendarError("Calendar sync could not finish. Existing events are retained; retry after checking the connection.") from None
        finally:
            if owned_provider and provider is not None:
                provider.close()
            store.close()


def calendar_status(config):
    store = Store(config.data_dir)
    try:
        return {"connected": (config.data_dir / "calendar-credentials.bin").exists(),
                "calendar": "FiveK Training", "last_sync": store.setting("calendar_sync"),
                "automation": store.setting("automation_status"), "last_cycle": store.setting("automation_cycle"),
                "last_error": store.setting("calendar_sync_error")}
    finally:
        store.close()
