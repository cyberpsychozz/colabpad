#!/usr/bin/env bash
set -euo pipefail
cd /opt/colabpad
sudo -u colabpad git pull --ff-only
sudo -u colabpad .venv/bin/pip install -r requirements.txt
(cd web && sudo -u colabpad npm ci && sudo -u colabpad npm run build)
sudo systemctl restart colabpad
curl --retry 10 --retry-delay 1 --retry-connrefused -fsS https://collabpad.ru/api/health
