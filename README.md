# FiveK

A Garmin onboarding, training-insights and adaptive 5K planning dashboard. Imports training and recovery history, stores source responses in SQLite, and generates a refreshable plan. Includes optional Apple Calendar integration.

## Run locally

Requires Python 3.12+. Install with `pip install .`, configure your own credentials using `.env.example`, then run `fivek refresh` for initial authentication and `fivek serve`. Open http://127.0.0.1:8765. Set FIVEK_AGE, FIVEK_HEIGHT_CM and FIVEK_SEX for your own profile; defaults are examples. The goal defaults to 31 January 2027; saved target preferences take precedence.

## Mobile dashboard and background imports

The dashboard has Today, Training plan and Insights views, with a desktop sidebar and compact mobile navigation. Today opens the current workout in one tap. Workout details focus on the session, run stages, gym exercises and working sets; coach, fuelling, rationale, progression-rule and recorded-activity pages have been removed. Insights is one six-chart dashboard with 28-day, 90-day, one-year and all-saved-history ranges. Charts cover 5K estimates, running volume, training load, body mass, HRV and sleep, with related fitness and composition readings alongside them. Selecting a chart enlarges it without adding reading lists or text pages. Running volume is aggregated from the full saved activity history, rather than the 26-week summary. Each metric uses its available saved dates; selecting a longer range never backfills Garmin history. There are no modal dialogs, data-entry forms or refresh controls. Opening the dashboard reads saved SQLite reports and never starts Garmin authentication or an import. The viewer checks local status every minute (every 15 seconds during an import), pauses while hidden and uses report ETags.

The deployed systemd timer refreshes every two hours independently of browsers. Local Windows installations can use `fivek schedule`. Initial activity discovery establishes membership once. Later discovery stops on a wholly known page older than a seven-day overlap and preserves older membership. Today and yesterday's recovery readings are fresh for two hours; the rest of the seven-day correction window is fresh for 24 hours. Cached older readings and unchanged workout details are reused. New or changed workouts receive details; old missing details are limited to 20 workouts and older missing recovery to 14 days per cycle. Backfill checkpoints resume on the next timer cycle and are reported as `backfilling`, not failures. Garmin rate limits still stop a run immediately.

An administrator can use `fivek refresh --full` to reconcile edits, deletions or backdated uploads beyond the overlap window. Routine page views and scheduled imports never request a full refetch. Account connection, preferences and optional calendar setup remain available through authenticated administrative APIs; Garmin MFA can be completed through the local CLI.

## Privacy and deployment

### Returning-lifter and football-effort preferences

`strength_experience=returning` treats prior lifting skill as owner-reported context. The two main sessions become squat/bench/row and deadlift/press/pull programmes, with 2–3 submaximal working sets, longer rests, ramp-up sets and explicit load progression. With `gym_sessions=3`, Wednesday adds an optional upper-compound session; Tuesday and Saturday remain the two baseline sessions, Friday remains rest, and Saturday is a double day. Recovery overrides still apply. The owner can set Monday football to hard and Sunday to moderate through `football_intensity`; this changes scheduling assumptions without rewriting recorded load or recovery data.

The owner-selected `weight_strategy=modest_reduction` supports an optional 1–2% review range from a saved `weight_reference_kg`, so the target does not ratchet downward after each measurement. It is enabled only with fresh measurements, an adequate scale-reported BMI, normal recovery and more than three weeks to the goal; otherwise the view returns to maintenance. It is not an ideal-racing-weight claim, calorie prescription or seconds-per-kilogram model. Strength and training quality take precedence, and the final three weeks use maintenance.

## Coaching and athlete context

The default mix is six training days: Monday/Sunday football, Tuesday runner-strength, Wednesday easy running plus upper-body/trunk gym, Thursday running quality when earned, Saturday longer easy running, and Friday full rest. The first two programme weeks rebuild running consistency. Timed running increases gradually, every fourth week consolidates, later eligible sessions alternate short intervals/tempo/5K work, and the final ten days taper. Current quality requires six running dates and 8 km/week, two programme weeks, recovered observations, suitable spacing and enough session budget. Future quality is conditional and is re-evaluated before becoming near-term. Gym prescriptions include sets, reps, rests, technique and progression; working kilograms are never invented. Recorded runs and strength sessions are tracked separately on double-session days.

Scale composition and Garmin race-prediction history use two bounded calls per two-hour cycle: 90 days on first import, then a seven-day overlap. Normalized observations are retained locally, including older history. Page navigation still makes no Garmin calls. Recovery pages compare seven-day sleep/HRV/resting-HR against personal baselines and explain acute/chronic device load; ratios are descriptive, not injury predictions. A percentage increase from a near-zero running baseline no longer creates a recovery flag by itself.

The current 5K outlook uses a fresh watch prediction or recent measured race/time trial, with ordinary runs shown separately. January scenarios assume 0.25% improvement per remaining week, capped at 4%, with a stretch cap of 6% and a 5%-slower interrupted-training case. These assumptions are explicit, unvalidated planning scenarios, not calibrated confidence intervals. Recovery pauses remove the central improvement. Pace ranges are provisional and effort takes precedence. No weight-loss speed benefit is assumed. Scale data supports weight/body-fat/muscle/water trends and a provisional ±1% maintenance band around a fresh seven-day average; it does not establish an ideal racing weight. Fuel training and seek qualified nutrition review before pursuing a weight-loss goal.

## Privacy and deployment

Source code is public; deployed dashboards are private to the configured
Tailscale owner. No personal databases, access tokens, credentials or imported
health records are included. Runtime state is excluded from Git. See
[deployment instructions](deploy/README.md) for always-on Linux hosting.

Training heuristics are transparent planning aids, not diagnoses or a guarantee of race performance. Garmin support depends on the third-party garminconnect adapter and account availability. Garmin can be configured through environment variables or the private connection API. Saved Garmin and optional calendar credentials use Fernet encryption with a separate private key file on Linux and account encryption on Windows.

## Checks

`PYTHONPATH=src python -m unittest discover -s tests -v`. Checks use offline fixtures rather than contacting Garmin.
