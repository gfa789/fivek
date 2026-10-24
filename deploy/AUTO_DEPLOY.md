# Automatic deployments

This host checks both public repositories' **main** branches every minute.
Pushing or merging to main starts deployment on the next check. Feature branches
are not deployed. Builds may take additional time.

## Deployment sequence

1. Fetch the current main revision from GitHub.
2. Build in a new release directory as a dedicated unprivileged build account,
   without production credentials or data. Rookcursion runs its tests and Vite
   production build. FiveK installs a separate Python environment and runs its
   application, credential-storage, Linux-restart, and owner-proxy tests.
3. Wait for an active chess analysis or FiveK import to finish.
4. Take a consistent private SQLite backup and atomically select the new release.
5. Restart the app and check its HTTP status and owner-only access bridge.
6. Restore the previous code and dependency environment if activation or health
   checks fail. A failed build leaves the running release in place.

Runtime databases, account credentials, Tailscale configuration, and service
configuration remain outside releases. Application rollback does not reverse
schema/data migrations. Pre-deployment database snapshots are available for
manual recovery under /var/backups/personal-apps; the latest five are retained.
Current and recent release directories are retained for code rollback.

## Operations

The host runs personal-apps-deploy.timer and personal-apps-deploy.service.
It uses outbound GitHub access; application access remains Tailscale-only.
No GitHub deployment token or public webhook is required.

Read logs with:

```sh
sudo journalctl -u personal-apps-deploy.service -n 100 --no-pager
sudo cat /var/lib/personal-apps-deploy/rookcursion.json
sudo cat /var/lib/personal-apps-deploy/fivek.json
```

A failed revision is retried after 15 minutes. A different main revision is
eligible immediately. To retry manually:

```sh
sudo python3 /usr/local/sbin/personal-apps-auto-deploy.py all --retry
```

To pause automatic deployment while diagnosing an issue:

```sh
sudo systemctl stop personal-apps-deploy.timer
```

The reproducible host installation files are in deploy/automation. They are
installed by an administrator, not replaced automatically from application
commits. Changes to service units or deployment-manager code need a deliberate
host installation. The timer deploys app code, dependencies, assets, and tests.

The initial installation of install-auto-deploy.sh expects its companion files
in /root. It creates isolated build users, verifies the rollback tests and
systemd units, and enables the timer. Existing apps must already be configured
as described in deploy/README.md.
