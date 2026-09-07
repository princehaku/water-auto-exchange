#!/usr/bin/env bash
set -euo pipefail
root=/apps/water-auto-exchange
exec "$root/runtime/bin/docker" --host unix:///run/water-docker/docker.sock "$@"
