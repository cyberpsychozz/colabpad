#!/usr/bin/env bash
set -euo pipefail
cd /opt/collabpad
sudo -u collabpad git pull --ff-only
sudo -u collabpad .venv/bin/pip install -r requirements.txt
(cd web && sudo -u collabpad npm ci && sudo -u collabpad npm run build)
sudo systemctl restart collabpad
curl --retry 10 --retry-delay 1 --retry-connrefused -fsS https://collabpad.ru/api/health
