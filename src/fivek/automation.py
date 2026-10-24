"""Two-hour Windows scheduling and an independent, recoverable refresh cycle."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from .config import Config
from .store import Store, now, file_lock, BusyError, refresh_active


def run_cycle(config: Config, auth_factory=None, calendar_provider=None):
    from garmin_api import GarminAuth, GarminError, GarminMFARequired
    from .pipeline import refresh, report
    from .calendar_sync import sync_calendar, CalendarError
    with file_lock(config.data_dir, "automation.lock", "Automatic refresh is already running."):
        status = {"started_at": now(), "status": "running", "message": "Refreshing Garmin"}
        store = Store(config.data_dir)
        try:
            store.set_setting("automation_cycle", status)
            if refresh_active(config.data_dir):
                status.update(status="deferred", message="Another refresh is running; the next cycle will retry.")
                return status
            try:
                from .account import auth_from_store
                api = (auth_factory or (lambda: auth_from_store(config)))().login()
                result = refresh(api, config, progress=lambda _: None)
                status["garmin_status"] = result["sync"]["run"]["status"]
                if status["garmin_status"] != "complete":
                    status.update(status="retry", message="Some Garmin resources remain incomplete; the next cycle will resume them.")
            except GarminMFARequired:
                status.update(status="needs_auth", message="Garmin requires verification. Complete a refresh in the dashboard.")
                result = report(store, config)
            except BusyError:
                status.update(status="deferred", message="Another refresh is running; the next cycle will retry.")
                return status
            except GarminError:
                status.update(status="retry", message="Garmin could not refresh. Saved data is retained; the next cycle will retry.")
                result = report(store, config)
            # Reprocessing also notices local-day changes and unrecorded past sessions.
            try:
                status["calendar"] = sync_calendar(config, result, calendar_provider)
            except CalendarError as exc:
                status.update(status="calendar_retry", message=str(exc))
            except BusyError:
                status.update(status="calendar_deferred", message="Calendar sync is already running; the next cycle will retry.")
            if status["status"] == "running":
                status.update(status="complete", message="Refresh and upcoming-week calendar reconciliation completed" if status["calendar"].get("connected") else "Garmin refreshed; connect Apple Calendar in the dashboard to publish runs")
            return status
        except Exception:
            status.update(status="retry", message="Automatic cycle could not finish. Saved data is retained; retry in the dashboard.")
            raise
        finally:
            status["finished_at"] = now()
            store.set_setting("automation_cycle", status)
            store.close()


def install_schedule(config: Config):
    if os.name != "nt":
        raise RuntimeError("Automatic installation currently supports Windows Task Scheduler.")
    workspace = Path.cwd().resolve()
    name = "FiveK-Refresh-" + hashlib.sha256(str(workspace).encode()).hexdigest()[:12]
    script = config.data_dir.resolve() / "scheduled-refresh.ps1"
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    paths = [workspace / "src", workspace / "data/fivek/calendar-runtime"]
    body = "$ErrorActionPreference = 'Stop'\nSet-Location -LiteralPath " + quote(workspace) + "\n"
    body += "$env:PYTHONPATH = " + quote(";".join(str(p) for p in paths)) + "\n"
    body += "$env:FIVEK_DATA_DIR = " + quote(config.data_dir.resolve()) + "\n"
    body += "& " + quote(sys.executable) + " -m fivek scheduled-refresh --quiet *> " + quote(config.data_dir.resolve() / "scheduled-refresh.log") + "\nexit $LASTEXITCODE\n"
    script.write_text(body, encoding="utf-8-sig")
    setup = config.data_dir.resolve() / "install-schedule.ps1"
    action_args = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + str(script) + '"'
    commands = ["$ErrorActionPreference = 'Stop'",
        "$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name",
        "$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument " + quote(action_args) + " -WorkingDirectory " + quote(workspace),
        "$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Hours 2)",
        "$logon = New-ScheduledTaskTrigger -AtLogOn -User $identity",
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)",
        "$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited",
        "Register-ScheduledTask -TaskName " + quote(name) + " -Action $action -Trigger @($trigger,$logon) -Settings $settings -Principal $principal -Description 'Refresh Garmin and reconcile the coming week of FiveK runs every two hours.' -Force | Out-Null",
        "Get-ScheduledTask -TaskName " + quote(name) + " | Select-Object TaskName,State | ConvertTo-Json -Compress"]
    setup.write_text("\n".join(commands), encoding="utf-8-sig")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-File", str(setup)],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError("Windows could not register the two-hour task. Run fivek schedule from your normal Windows account.")
    status = {"installed": True, "task_name": name, "interval_hours": 2, "installed_at": now(),
              "message": "Runs every two hours and at sign-in, while this PC is awake and your Windows account is signed in. Overlapping runs are skipped; interrupted cycles retry."}
    store = Store(config.data_dir)
    try:
        store.set_setting("automation_status", status)
    finally:
        store.close()
    return status
