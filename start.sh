#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ ! -f .venv/bin/python ]]; then
    printf '%s\n' 'Нужно подготовить окружение. Инструкция: LOCAL.md'
    exit 1
fi
if [[ ! -f app/static/index.html ]]; then
    (cd web && npm ci && npm run build)
fi
if [[ ! -f .env ]]; then
    cp .env.example .env
fi
exec env -u PYTHONPATH .venv/bin/python -m app
