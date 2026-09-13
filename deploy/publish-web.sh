#!/usr/bin/env bash
# Publish a local static bundle without restarting the device API or Nginx.
set -euo pipefail
root=/apps/water-auto-exchange
cd "$root"
test "$(pwd -P)" = "$root"
backup="$root/backups/$(date -u +%Y%m%dT%H%M%SZ)-web-$$"
install -d -m 700 "$backup" "$backup/staged"
cp -a "$root/www/water" "$backup/live"
cp -a "$root/deploy/www" "$backup/source"
cp -a "$root/deploy/install.sh" "$backup/install.sh"
cp -a "$root/docs/web-console.md" "$backup/web-console.md"
tar -xzf "$root/web-update.tar.gz" -C "$backup/staged"
files=(index.html health.json aquarium.html aquarium.css aquarium.js aquarium-scene.js vendor/three.module.js vendor/THREE-LICENSE.txt)
for name in "${files[@]}"; do test -f "$backup/staged/deploy/www/$name"; done
cmp "$backup/staged/deploy/www/index.html" "$backup/staged/deploy/www/aquarium.html"
rollback() {
  trap - ERR
  cp -a "$backup/live/." "$root/www/water/"
  cp -a "$backup/source/." "$root/deploy/www/"
  cp -a "$backup/install.sh" "$root/deploy/install.sh"
  cp -a "$backup/web-console.md" "$root/docs/web-console.md"
  echo "Static update rolled back. Backup: $backup" >&2
}
trap rollback ERR
for name in "${files[@]}"; do
  install -D -m 644 "$backup/staged/deploy/www/$name" "$root/deploy/www/$name"
  install -D -m 644 "$backup/staged/deploy/www/$name" "$root/www/water/$name"
  cmp "$root/deploy/www/$name" "$root/www/water/$name"
done
# The sole entry is now the 3D console; remove the retired UI assets only.
rm -f -- "$root/www/water/app.js" "$root/www/water/style.css" "$root/deploy/www/app.js" "$root/deploy/www/style.css"
install -m 644 "$backup/staged/deploy/install.sh" "$root/deploy/install.sh"
install -m 644 "$backup/staged/docs/web-console.md" "$root/docs/web-console.md"
install -m 644 "$backup/staged/deploy/publish-web.sh" "$root/deploy/publish-web.sh"
curl -fsS http://127.0.0.1:8790/water/api/health >/dev/null
trap - ERR
echo "Static site published. Backup: $backup"
