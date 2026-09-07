#!/usr/bin/env bash
set -euo pipefail
root=/apps/water-auto-exchange
cd "$root"
test "$(pwd -P)" = "$root"
test -f /etc/nginx/snippets/water-auto-exchange.conf
bash deploy/setup-docker.sh
docker() { bash "$root/deploy/docker.sh" "$@"; }
if ! docker image inspect python:3.12-slim >/dev/null 2>&1; then
    if command -v podman >/dev/null && podman image exists docker.1ms.run/library/python:3.12-slim; then
        podman save docker.1ms.run/library/python:3.12-slim | docker load
        docker tag docker.1ms.run/library/python:3.12-slim python:3.12-slim
    else
        docker pull python:3.12-slim
    fi
fi
stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
image="water-console:$stamp"
# Exclude runtime, data, backups and credentials from the build context.
tar -cf - server deploy/Dockerfile | DOCKER_BUILDKIT=0 "$root/runtime/bin/docker" --host unix:///run/water-docker/docker.sock build --network host -f deploy/Dockerfile -t "$image" -
backup="$root/backups/$stamp"
install -d -m 700 "$backup" "$root/config"
cp -L /etc/nginx/snippets/water-auto-exchange.conf "$backup/nginx-water.conf"
if [ -d "$root/www" ]; then cp -a "$root/www" "$backup/www"; fi
old_image=$(docker inspect --format '{{.Config.Image}}' water-console 2>/dev/null || true)
printf '%s\n' "$old_image" > "$backup/previous-image.txt"
old_active=0
if systemctl is-active --quiet water-console; then old_active=1; fi
run_container() {
    docker run -d --name water-console --restart unless-stopped --network host \
        --read-only --cap-drop ALL --security-opt no-new-privileges \
        --tmpfs /tmp:rw,noexec,nosuid,size=16m --log-opt max-size=5m --log-opt max-file=3 \
        --env-file "$root/config/water.env" -v "$root/data:/data" "$1"
}
rollback() {
    trap - ERR
    docker rm -f water-console >/dev/null 2>&1 || true
    cp "$backup/nginx-water.conf" "$root/config/nginx-water.conf"
    ln -sfn "$root/config/nginx-water.conf" /etc/nginx/snippets/water-auto-exchange.conf
    if [ -d "$backup/www" ]; then cp -a "$backup/www/." "$root/www/"; fi
    if [ -n "$old_image" ]; then run_container "$old_image"; fi
    if [ "$old_active" = 1 ]; then systemctl start water-console; fi
    nginx -t && systemctl reload nginx
    echo "Deployment failed; restored previous service. Backup: $backup" >&2
}
trap rollback ERR
if [ ! -f "$root/config/water.env" ]; then
    install -m 600 /etc/water-console.env "$root/config/water.env"
fi
# Stop the existing writer before copying SQLite and its WAL together.
if [ "$old_active" = 1 ]; then systemctl stop water-console; fi
if [ -n "$old_image" ]; then docker stop water-console; fi
install -d -m 700 -o 10001 -g 10001 "$root/data"
if [ ! -f "$root/data/water.db" ]; then cp -a /var/lib/water-console/. "$root/data/"; fi
cp -a "$root/data" "$backup/data"
chown -R 10001:10001 "$root/data"
chmod 700 "$root/data"
docker rm water-console >/dev/null 2>&1 || true
run_container "$image"
for attempt in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8790/water/api/health >/dev/null; then break; fi
    sleep 1
done
curl -fsS http://127.0.0.1:8790/water/api/health >/dev/null
install -d -m 755 "$root/www/water"
for name in index.html health.json style.css app.js; do install -m 644 "deploy/www/$name" "$root/www/water/$name"; done
install -m 644 deploy/nginx-water.conf "$root/config/nginx-water.conf"
ln -sfn "$root/config/nginx-water.conf" /etc/nginx/snippets/water-auto-exchange.conf
nginx -t
systemctl reload nginx
systemctl disable water-console 2>/dev/null || true
trap - ERR
printf 'Deployed Docker container water-console. Backup: %s\n' "$backup"
