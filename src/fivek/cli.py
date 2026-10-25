from __future__ import annotations

import argparse
import logging
import json
import sys

from garmin_api import GarminAuth, GarminError, GarminMFARequired
from .config import Config, load_environment
from .pipeline import refresh, report
from .store import Store


def main() -> int:
    parser = argparse.ArgumentParser(description="Garmin refresh → training insights → adaptive end-of-January 5K plan")
    parser.add_argument("command", choices=("refresh", "report", "serve", "status", "doctor", "backup", "cancel", "schedule", "scheduled-refresh", "calendar-sync"), nargs="?", default="serve")
    parser.add_argument("--full", action="store_true", help="Refetch all supported history, including cached detail and daily data")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--quiet", action="store_true", help="Show only connection and final refresh status")
    args = parser.parse_args()
    load_environment()
    # Library login diagnostics can contain auth context. Only our safe errors are shown.
    logging.getLogger("garminconnect").setLevel(logging.CRITICAL)
    try:
        config = Config.environment()
        if args.command in {"schedule", "scheduled-refresh", "calendar-sync"}:
            if args.command == "schedule":
                from .automation import install_schedule
                value = install_schedule(config)
            elif args.command == "scheduled-refresh":
                from .automation import run_cycle
                value = run_cycle(config)
            else:
                from .calendar_sync import sync_calendar
                store = Store(config.data_dir)
                try:
                    value = sync_calendar(config, report(store, config))
                finally:
                    store.close()
            print(json.dumps(value, indent=2))
            return 0 if value.get("status", "complete") in {"complete", "backfilling"} else 1
        if args.command in {"status", "doctor", "backup", "cancel"}:
            from .maintenance import create_backup, diagnostics, request_cancel
            if args.command == "cancel":
                print("Cancellation requested; saved checkpoints are retained." if request_cancel(config) else "No Garmin refresh is running.")
            elif args.command == "backup":
                print("Database backup created: " + create_backup(config)["path"])
            else:
                result = diagnostics(config, check_database=args.command == "doctor")
                print(json.dumps(result, indent=2))
                return 0 if result.get("database_check", "ok") == "ok" else 1
        elif args.command == "serve":
            from .server import serve
            serve(config, args.port)
        elif args.command == "report":
            store = Store(config.data_dir)
            try:
                result = report(store, config)
            finally:
                store.close()
            print(f"Plan generated for {result['plan']['target_date']}: {config.data_dir / 'insights.md'}")
        else:
            auth = GarminAuth.from_environment()
            print("Connecting to Garmin...", flush=True)
            try:
                api = auth.login()
            except GarminMFARequired:
                if not sys.stdin.isatty():
                    print("Garmin requires MFA. Open the dashboard and submit the verification code there.", file=sys.stderr)
                    return 2
                from getpass import getpass
                api = auth.complete_mfa(getpass("Garmin verification code: "))
            result = refresh(api, config, full=args.full, progress=(lambda _: None) if args.quiet else (lambda message: print(message, flush=True)))
            if (config.data_dir / "calendar-credentials.bin").exists():
                from .calendar_sync import sync_calendar, CalendarError
                try:
                    sync_calendar(config, result)
                except CalendarError as exc:
                    print("Garmin refresh saved. " + str(exc), file=sys.stderr)
                    return 1
            print(f"Refresh {result['sync']['run']['status']}. Insights: {config.data_dir / 'insights.md'}")
            return 0 if result["sync"]["run"]["status"] in {"complete", "backfilling"} else 1
    except KeyboardInterrupt:
        print("Stopped. Saved Garmin data will resume on refresh.")
        return 130
    except (GarminError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("Operation failed. Saved data is retained; check local configuration and retry.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
