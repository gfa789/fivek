# FiveK

A Garmin onboarding, training-insights and adaptive 5K planning dashboard. Imports training and recovery history, stores source responses in SQLite, and generates a refreshable plan. Includes optional Apple Calendar integration.

## Run locally

Requires Python 3.12+. Install with `pip install .`, configure your own credentials using `.env.example`, then run `fivek serve`. Open http://127.0.0.1:8765. Refresh Garmin and complete MFA in the dashboard if requested. Set FIVEK_AGE, FIVEK_HEIGHT_CM and FIVEK_SEX for your own profile; defaults are examples.

## Privacy and deployment

Source code is public; deployed dashboards are private to the configured
Tailscale owner. No personal databases, access tokens, credentials or imported
health records are included. Runtime state is excluded from Git. See
[deployment instructions](deploy/README.md) for always-on Linux hosting.

Training heuristics are transparent planning aids, not diagnoses or a guarantee of race performance. Garmin support depends on the third-party garminconnect adapter and account availability. Garmin can be connected from the dashboard Settings section, or configured through environment variables. Saved Garmin and optional calendar credentials use Fernet encryption with a separate private key file on Linux and account encryption on Windows.

## Checks

`PYTHONPATH=src python -m unittest discover -s tests -v`. Checks use offline fixtures rather than contacting Garmin.
