#!/bin/bash
set -euo pipefail
install -d -m 0755 /var/lib/personal-apps-deploy /var/lib/personal-apps-deploy/build
install -d -m 0700 /var/backups/personal-apps
for app in rookcursion fivek; do
    if ! id "$app-build" >/dev/null 2>&1; then
        useradd --system --home-dir "/var/lib/personal-apps-deploy/build/$app" --shell /usr/sbin/nologin "$app-build"
    fi
    install -d -o "$app-build" -g "$app-build" -m 0700 "/var/lib/personal-apps-deploy/build/$app"
    install -d -m 0755 "/opt/$app/releases"
done
install -m 0755 /root/auto-deploy.py /usr/local/sbin/personal-apps-auto-deploy.py
install -m 0644 /root/personal-apps-deploy.service /etc/systemd/system/
install -m 0644 /root/personal-apps-deploy.timer /etc/systemd/system/
python3 /root/test_auto_deploy.py -v
systemd-analyze verify /etc/systemd/system/personal-apps-deploy.service /etc/systemd/system/personal-apps-deploy.timer
systemctl daemon-reload
systemctl enable --now personal-apps-deploy.timer
systemctl start --no-block personal-apps-deploy.service
