# Private Linux deployment

Run on Ubuntu 24.04 with persistent storage. Both application listeners bind only
to loopback. Tailscale Serve supplies HTTPS and authenticated identity. Never use
Tailscale Funnel or open public application ports.

Install this repository at `/opt/fivek/app`, create the locked `fivek` service
account, and create `/etc/fivek/app.env` from `app.env.example`. Keep that file
root-owned, mode 0600. State lives at `/var/lib/fivek`, owned by `fivek`, mode
0700. Install the service units from this directory into `/etc/systemd/system/`.

Create `/opt/fivek/venv` using Python 3.12+, then install this repository there using `pip install .`. Generate a Fernet key into `/etc/fivek/calendar.key`, readable only by the fivek service account (0600). Reconnect Apple Calendar: Windows-encrypted credentials are not portable.

Start the services with systemd and enable them at boot. Configure the server's
Tailscale identity as `personal-apps`, authenticated to your own account. Then:

```sh
tailscale serve --bg --https=443 http://127.0.0.1:8876
```

Set `FIVEK_BASE_PATH=/running/` in the shared environment file to serve FiveK at `/running/` and a lightweight app directory at `/` on port 443. The directory links to Running and Rookcursion (`/chess/`, configured separately in Tailscale Serve). Without this setting, standalone FiveK stays at `/`. Enable fivek-proxy.service. Enable fivek-refresh.timer and fivek-backup.timer after Garmin authentication works.
Set `TAILSCALE_OWNER` to your Tailscale login; the application/bridge rejects
missing and different identities, even if other tailnet members can reach it.
Only Tailscale Serve should connect to these loopback ports. Do not share server
shell access with people who should not access app data.

Validate dashboard, POST token checks, saved state after restart, wrong-identity
403 responses, and a failed connection to public ports before retiring the laptop
setup. Keep the original laptop data until migration is verified. Use SQLite's
online backup API for a consistent migration snapshot rather than copying a live
database file. Keep credentials and data out of Git and out of web roots.

Garmin may challenge or reject cloud IP logins. Authenticate from the dashboard
after credentials are configured privately. A successful home-network login
does not guarantee a cloud login succeeds.

## Fresh account setup

The dashboard Settings section includes an encrypted Garmin connection form. Save the connection, then select Refresh Garmin and complete MFA if requested. Optional Apple Calendar setup uses an app-specific password. No laptop credentials or history are required. The scheduled-refresh service loads the same saved encrypted connection.

Set an automation_status database setting with installed=true and platform=linux when the systemd refresh timer is enabled, so the dashboard describes server scheduling correctly.
