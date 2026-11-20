#!/usr/bin/env bash
# Pull, rebuild and restart the Compose project, then check health. Run as root on the server.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose up --build -d --wait
port="$(docker compose port app 8000 | cut -d: -f2)"
curl --retry 10 --retry-delay 1 --retry-connrefused -fsS "http://127.0.0.1:${port}/api/health"
echo
