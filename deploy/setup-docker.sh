#!/usr/bin/env bash
# Dedicated Docker daemon: keep the existing Podman workloads and networking intact.
set -euo pipefail
root=/apps/water-auto-exchange
mkdir -p "$root/runtime/bin" "$root/runtime/downloads"
if [ ! -x "$root/runtime/bin/dockerd" ]; then
    curl -fL --retry 3 --connect-timeout 15 -o "$root/runtime/downloads/docker.tgz" https://download.docker.com/linux/static/stable/x86_64/docker-29.8.0.tgz
    tar -xzf "$root/runtime/downloads/docker.tgz" --strip-components=1 -C "$root/runtime/bin"
fi
cat > "$root/runtime/water-docker.service" <<'UNIT'
[Unit]
Description=Dedicated Docker Engine for water-auto-exchange
After=network-online.target
Wants=network-online.target
[Service]
Environment=PATH=/apps/water-auto-exchange/runtime/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
ExecStart=/apps/water-auto-exchange/runtime/bin/dockerd --host=unix:///run/water-docker/docker.sock --data-root=/apps/water-auto-exchange/runtime/data --exec-root=/run/water-docker --pidfile=/run/water-docker/docker.pid --bridge=none --iptables=false --ip6tables=false --ip-forward=false --ip-masq=false
Restart=always
RestartSec=3
Delegate=yes
KillMode=process
[Install]
WantedBy=multi-user.target
UNIT
ln -sfn "$root/runtime/water-docker.service" /etc/systemd/system/water-docker.service
systemctl daemon-reload
systemctl enable --now water-docker
for attempt in $(seq 1 30); do
    if bash "$root/deploy/docker.sh" info >/dev/null 2>&1; then exit 0; fi
    sleep 1
done
exit 1
