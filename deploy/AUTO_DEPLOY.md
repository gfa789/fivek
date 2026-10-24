# GitHub Actions deployments

Push to `main` to run tests and deploy. Pull requests run tests only; manual workflow runs on main are supported. See `.github/workflows/deploy.yml` for the workflow and the Actions tab for logs.

The workflow joins Tailscale using an ephemeral OIDC identity restricted to this repository and main branch, and uses a repository-specific forced-command SSH key to request the exact commit. It cannot use an interactive shell or deploy another app. The CI network tag can reach only the deployment server's SSH port. No public app ports are opened.

The server independently verifies main's head, builds/tests without production data, and switches releases only when the app is idle. Failed builds retain the current version; failed health checks restore the previous release. Databases and credentials remain outside releases. Pre-deploy database snapshots are retained; schema/data migrations are not automatically reversed.

Required repository variables: TS_CLIENT_ID, TS_AUDIENCE, DEPLOY_KNOWN_HOSTS. Secret: DEPLOY_SSH_KEY. Actions are pinned to official commit revisions. Privileged host infrastructure is installed separately; pushing this repository cannot replace the root-owned manager.

The shared homepage and canonical deployment infrastructure now live in https://github.com/gfa789/personal-apps-home. The old minute-by-minute polling timer is disabled. Administrator recovery command: `sudo python3 /usr/local/sbin/personal-apps-auto-deploy.py APP --retry --wait`.

GitHub uses immutable account/repository IDs in this account's OIDC subjects; the Tailscale trust uses the exact issued main-branch identity. Every deployment also checks that its runner cannot connect to private app HTTPS.
