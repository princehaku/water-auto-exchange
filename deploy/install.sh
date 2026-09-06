#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
site=/etc/nginx/conf.d/bytegallop.conf
snippet=/etc/nginx/snippets/water-auto-exchange.conf
web=/var/www/water-auto-exchange/water
app=/opt/water-console/app.py
unit=/etc/systemd/system/water-console.service
stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
backup="/root/apps/water-auto-exchange/backups/$stamp"
test -f "$site"
mkdir -p "$backup" /etc/nginx/snippets "$web" /opt/water-console
cp -p "$site" "$backup/bytegallop.conf"
if [ -f "$snippet" ]; then cp -p "$snippet" "$backup/nginx-water.conf"; fi
cp -a "$web" "$backup/web"
if [ -f "$app" ]; then cp -p "$app" "$backup/app.py"; fi
if [ -f "$unit" ]; then cp -p "$unit" "$backup/water-console.service"; fi
python3 - "$backup" <<'PY'
import os
import sqlite3
import sys
source = '/var/lib/water-console/water.db'
if os.path.isfile(source):
    # backup() arrived in Python 3.7; use the SQLite CLI-independent SQL dump on 3.6.
    connection = sqlite3.connect(source)
    destination = os.path.join(sys.argv[1], 'water-db.sql')
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output:
        for statement in connection.iterdump():
            output.write(statement + '\n')
    connection.close()
PY
was_active=0
if systemctl is-active --quiet water-console; then was_active=1; fi
rollback() {
    trap - ERR
    cp -p "$backup/bytegallop.conf" "$site"
    if [ -f "$backup/nginx-water.conf" ]; then cp -p "$backup/nginx-water.conf" "$snippet"; else rm -f -- "$snippet"; fi
    for name in index.html health.json style.css app.js; do
        if [ -f "$backup/web/$name" ]; then cp -p "$backup/web/$name" "$web/$name"; else rm -f -- "$web/$name"; fi
    done
    if [ -f "$backup/app.py" ]; then cp -p "$backup/app.py" "$app"; fi
    if [ -f "$backup/water-console.service" ]; then
        cp -p "$backup/water-console.service" "$unit"
        systemctl daemon-reload
        if [ "$was_active" = 1 ]; then systemctl restart water-console; else systemctl stop water-console; fi
    else
        systemctl disable --now water-console || true
        rm -f -- "$unit"
        systemctl daemon-reload
    fi
    nginx -t && systemctl reload nginx
    printf 'Installation failed; restored backup: %s\n' "$backup" >&2
}
trap 'rollback' ERR
if ! id waterconsole >/dev/null 2>&1; then useradd --system --no-create-home --shell /sbin/nologin waterconsole; fi
install -d -m 700 -o waterconsole -g waterconsole /var/lib/water-console
python3 - <<'PY'
import os
import secrets
path = '/etc/water-console.env'
if not os.path.exists(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write('WATER_ADMIN_KEY=' + secrets.token_hex(32) + '\n')
        f.write('WATER_DEVICE_KEY=' + secrets.token_hex(32) + '\n')
        f.write('WATER_ORIGIN=https://bytegallop.com\n')
PY
install -m 644 server/app.py "$app"
install -m 644 deploy/water-console.service "$unit"
systemctl daemon-reload
systemctl enable water-console
systemctl restart water-console
python3 - <<'PY'
import json
import time
import urllib.request
for attempt in range(20):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8790/water/api/health', timeout=2) as r:
            assert json.load(r)['ok'] is True
        break
    except Exception:
        if attempt == 19:
            raise
        time.sleep(0.5)
PY
install -m 644 deploy/nginx-water.conf "$snippet"
python3 - "$site" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
text = p.read_text()
include = '    include /etc/nginx/snippets/water-auto-exchange.conf;'
if include not in text:
    if 'location = /water' in text or 'location /water' in text or 'location ^~ /water' in text:
        raise SystemExit('Existing water route needs review before installation')
    anchor = '    # Central SMS device API and management console.'
    if text.count(anchor) != 1:
        raise SystemExit('Expected HTTPS insertion anchor was not found exactly once')
    p.write_text(text.replace(anchor, include + '\n\n' + anchor))
PY
for name in index.html health.json style.css app.js; do install -m 644 "deploy/www/$name" "$web/$name"; done
nginx -t
systemctl reload nginx
trap - ERR
printf 'Deployed water console. Backup: %s\n' "$backup"
