#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
site=/etc/nginx/conf.d/bytegallop.conf
snippet=/etc/nginx/snippets/water-auto-exchange.conf
web=/var/www/water-auto-exchange/water
stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
backup="/root/apps/water-auto-exchange/backups/$stamp"
test -f "$site"
mkdir -p "$backup" /etc/nginx/snippets "$web"
cp -p "$site" "$backup/bytegallop.conf"
if [ -f "$snippet" ]; then cp -p "$snippet" "$backup/nginx-water.conf"; fi
if [ -f "$web/index.html" ]; then cp -p "$web/index.html" "$backup/index.html"; fi
if [ -f "$web/health.json" ]; then cp -p "$web/health.json" "$backup/health.json"; fi
rollback() {
    cp -p "$backup/bytegallop.conf" "$site"
    if [ -f "$backup/nginx-water.conf" ]; then cp -p "$backup/nginx-water.conf" "$snippet"; else rm -f -- "$snippet"; fi
    for name in index.html health.json; do
        if [ -f "$backup/$name" ]; then cp -p "$backup/$name" "$web/$name"; else rm -f -- "$web/$name"; fi
    done
    nginx -t && systemctl reload nginx
}
trap 'rollback' ERR
install -m 644 deploy/nginx-water.conf "$snippet"
python3 - "$site" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
text = p.read_text()
include = "    include /etc/nginx/snippets/water-auto-exchange.conf;"
if include not in text:
    if "location = /water" in text or "location /water" in text or "location ^~ /water" in text:
        raise SystemExit("Existing water route needs review before installation")
    anchor = "    # Central SMS device API and management console."
    if text.count(anchor) != 1:
        raise SystemExit("Expected HTTPS insertion anchor was not found exactly once")
    p.write_text(text.replace(anchor, include + "\n\n" + anchor))
PY
install -m 644 deploy/www/index.html "$web/index.html"
install -m 644 deploy/www/health.json "$web/health.json"
nginx -t
systemctl reload nginx
trap - ERR
printf 'Deployed water project entry. Backup: %s\n' "$backup"
