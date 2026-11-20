#!/usr/bin/env bash
# Pull, rebuild and restart the Compose project, check health, then delete what builds leave
# behind (old untagged images and the build cache). Run as root on the server.
set -euo pipefail
cd "$(dirname "$0")/.."

# Below this the build and SQLite (which needs room for its WAL files) start failing.
MIN_FREE_MIB=1024
# Below this, clean up before building, not only after.
CLEAN_FIRST_MIB=3072

free_mib() {
    df -Pm "$(docker info -f '{{.DockerRootDir}}')" | awk 'NR == 2 { print $4 }'
}

cleanup() {
    # Never volumes: the database, the sprites and the backups live in one.
    docker image prune -f > /dev/null
    docker builder prune -f > /dev/null
}

if (( $(free_mib) < CLEAN_FIRST_MIB )); then
    echo "Low on disk ($(free_mib) MiB free); deleting old images and the build cache first."
    cleanup
fi
if (( $(free_mib) < MIN_FREE_MIB )); then
    echo "Only $(free_mib) MiB free on Docker's disk, need ${MIN_FREE_MIB}. Nothing was changed." >&2
    echo "See what takes the space: docker system df; du -xh --max-depth=1 / | sort -h" >&2
    exit 1
fi

# From here on, clean up however the update ends: a failed build leaves layers behind too.
trap 'cleanup; echo "Free on Docker'"'"'s disk: $(free_mib) MiB"' EXIT

git pull --ff-only
docker compose up --build -d --wait
port="$(docker compose port app 8000 | cut -d: -f2)"
curl --retry 10 --retry-delay 1 --retry-connrefused -fsS "http://127.0.0.1:${port}/api/health"
echo
