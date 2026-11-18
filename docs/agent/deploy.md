# Deploying Living Canvas to collabpad.ru

For the human. Nothing here has been run against the server by the agent: every command
below is yours to run. Work through it top to bottom; each step says how to check it.

The production layout is the one legacy used and is kept: Ubuntu VPS, system Nginx with
the certificates already issued, the app as the systemd service `collabpad` running from
`/opt/collabpad` as user `collabpad`, settings in `/etc/collabpad/collabpad.env`, data in
`/var/lib/collabpad`. A Docker variant is at the end.

What is new compared with legacy:

- **The database is replaced, not migrated.** The new app refuses to start on the old
  `collabpad.db` ("created by another version of CollabPad"). Step 3 moves it aside; it is
  kept, never deleted.
- `DATA_DIR/sprites/` holds the sprite sheets of drawings brought to life. It is backed up
  together with the database (`deploy/backup.py` now mirrors it to `backups/sprites/`).
- The app starts a second process (the world simulation). It lives and dies with the
  service; nothing to configure. The unit file changed (`KillMode=mixed`) so that stopping
  the service lets the simulation save the world first.
- skia-python needs three system libraries: `libfontconfig1 libgl1 libegl1`.
- New settings: `SMTP_*`, `PUBLIC_URL`, `VLM_API_KEY`, `YOOKASSA_*`, `ADS_BLOCK_*`.
- A daily retention pass (old ALIVE jobs, world events, sprite files of long-dead
  entities) runs inside the app. Strokes and payments are never removed.

Sizing: the load test (`docs/agent/loadtest.md`) ran 2 000 living entities and 50 clients in
about 140 MiB of memory, with the server using about half a core and the simulation a sixth
of one. Outgoing traffic is roughly 50–90 KiB/s per active player at that density.

---

## 0. Before the server: merge the branch

On your own machine, once you have reviewed `living-canvas`. `main` has no commits that
`living-canvas` lacks, so this is a fast-forward:

```bash
git checkout main
```

```bash
git merge --ff-only living-canvas
```

```bash
git push origin main
```

The tag `legacy-v1` (the old app) is already on GitHub; step 9 uses it to roll back.

## 1. Accounts and keys to have ready

| What | Where it goes | Without it |
|---|---|---|
| SMTP mailbox for sign-in links (implicit TLS, port 465) | `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_USER`, `SMTP_PASSWORD` | sign-in links are written to the service log instead of being mailed. **Do not run production like that**: anyone reading the log could sign in as anyone. |
| OpenRouter key | `VLM_API_KEY` | ALIVE still works: the author picks the type from a grid. Daily spend cap: `daily_budget_usd` in `config/vlm.yaml` (5 USD). |
| YooKassa **test** shop: shop id and a secret key that starts with `test_` | `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY` | checkout answers "Payment failed". A key not starting with `test_` is refused on purpose: the brief allows test mode only. |
| Yandex Advertising Network (RSYa) block ids `R-A-…` | `ADS_BLOCK_SIDE` (vertical, 160×600 / 300×600), `ADS_BLOCK_BOTTOM` (adaptive, narrow screens) | no ads. Ads also need `ads.enabled: true` in `config/flags.yaml` (step 7). |

## 2. Log in and install the new system packages

```bash
ssh <you>@<server>
```

```bash
sudo apt update && sudo apt install -y libfontconfig1 libgl1 libegl1
```

Python 3.12 and Node.js (20.19 or newer) are already there from legacy. Check:

```bash
python3.12 --version && node --version
```

## 3. Stop the old app and put its data aside

Take one last backup of the legacy database, then move the database and its backups into
`/var/lib/collabpad/legacy/`, where nothing will touch them.

```bash
sudo systemctl stop collabpad
```

```bash
sudo -u collabpad /opt/collabpad/.venv/bin/python /opt/collabpad/deploy/backup.py --directory /var/lib/collabpad/backups
```

```bash
sudo -u collabpad mkdir -p /var/lib/collabpad/legacy
```

```bash
sudo bash -c 'mv /var/lib/collabpad/collabpad.db* /var/lib/collabpad/legacy/ && mv /var/lib/collabpad/backups/collabpad-*.db /var/lib/collabpad/legacy/'
```

Check: `ls /var/lib/collabpad` shows `backups` (empty) and `legacy`, and no `collabpad.db`.

```bash
ls -la /var/lib/collabpad /var/lib/collabpad/legacy
```

## 4. Update the code

```bash
cd /opt/collabpad && sudo -u collabpad git fetch origin && sudo -u collabpad git checkout main && sudo -u collabpad git pull --ff-only
```

```bash
sudo -u collabpad .venv/bin/pip install -r requirements.txt
```

```bash
cd /opt/collabpad/web && sudo -u collabpad npm ci && sudo -u collabpad npm test && sudo -u collabpad npm run build
```

Check that skia-python loads with the new libraries:

```bash
sudo -u collabpad /opt/collabpad/.venv/bin/python -c "import skia, numpy, PIL; print('ok')"
```

## 5. Settings

Add the new lines to the existing env file. Compare it with the example first:

```bash
sudo diff /etc/collabpad/collabpad.env /opt/collabpad/deploy/collabpad.env.example
```

```bash
sudo nano /etc/collabpad/collabpad.env
```

It must contain at least (values from step 1):

```
PORT=8000
DATA_DIR=/var/lib/collabpad
PUBLIC_URL=https://collabpad.ru
CORS_ORIGINS=https://collabpad.ru
SMTP_HOST=...
SMTP_PORT=465
SMTP_FROM=...
SMTP_USER=...
SMTP_PASSWORD=...
VLM_API_KEY=...
YOOKASSA_SHOP_ID=...
YOOKASSA_SECRET_KEY=test_...
ADS_BLOCK_SIDE=
ADS_BLOCK_BOTTOM=
```

Remove `DB_PATH` if the old file sets one (empty means `DATA_DIR/collabpad.db`). Keep the
file private:

```bash
sudo chown root:collabpad /etc/collabpad/collabpad.env && sudo chmod 640 /etc/collabpad/collabpad.env
```

## 6. Install the new service file and start

```bash
sudo cp /opt/collabpad/deploy/systemd/collabpad.service /etc/systemd/system/collabpad.service
```

```bash
sudo cp /opt/collabpad/deploy/systemd/collabpad-backup.service /opt/collabpad/deploy/systemd/collabpad-backup.timer /etc/systemd/system/
```

```bash
sudo systemctl daemon-reload && sudo systemctl start collabpad && sudo systemctl enable --now collabpad-backup.timer
```

Check, in this order:

```bash
curl -fsS https://collabpad.ru/api/health
```

should print `{"ok":true}`.

```bash
sudo journalctl -u collabpad --since "5 min ago" --no-pager
```

should show no errors. Within a minute after the first visitor opens the world it shows a
line like `Simulation: 10 Hz, 0 entities (0 active), tick p50 … ms` (every minute after
that). A line saying "running at 5 Hz" means the server is too slow for 10 ticks a second;
tell the agent.

```bash
ls -la /var/lib/collabpad
```

should now show a fresh `collabpad.db` (plus `-wal`/`-shm`) and `sprites/` with three
built-in sprites (`builtin-apple`, `builtin-fish`, `builtin-flame`).

Nginx needs no change: `deploy/nginx/collabpad.conf` is byte for byte the legacy file
(WebSocket upgrade on `/socket.io/`, the `colabpad.ru` typo redirect with `utm_medium`). To
be sure the live copy matches, diff it against wherever you installed it, for example:

```bash
sudo diff /etc/nginx/sites-available/collabpad.conf /opt/collabpad/deploy/nginx/collabpad.conf
```

## 7. Flags

`config/flags.yaml` is read at startup:

```yaml
ads: {enabled: false}         # true: RSYa banners and the ad-blocker gate for free plans
billing: {recurring: false}   # true: the 03:00 MSK auto-renewal charges saved cards
vlm: {enabled: true}          # false: always the manual type grid
sleep_catchup: true
```

Change flags in the repository and deploy (`deploy/update.sh`), not by editing the file on
the server: a local edit makes the next `git pull --ff-only` fail. After changing a flag:

```bash
sudo /opt/collabpad/deploy/update.sh
```

Turn `ads.enabled` on only after the RSYa block ids are set and the blocks are approved;
with it on, free users with an ad blocker see the gate instead of the world.

## 8. YooKassa test shop

In the YooKassa dashboard of the **test** shop → Integration → HTTP notifications:

- URL: `https://collabpad.ru/api/billing/webhook`
- Events: `payment.succeeded`, `payment.canceled`

The app accepts notifications only from YooKassa's published addresses
(`webhook_ips` in `config/billing.yaml`; check them against YooKassa's docs once). Nginx
passes the real address in `X-Forwarded-For`, and Uvicorn runs with `--proxy-headers
--forwarded-allow-ips 127.0.0.1`, so the allowlist sees it. The app never trusts the
notification body: it fetches the payment from YooKassa by id first.

Check: sign in on the site, open `/plans`, pay with one of YooKassa's test cards for a
successful payment (their "Testing" docs list them; `5555 5555 5555 4477`, any future date,
any CVC, was one at the time of writing). You come back to `/billing/return`, the plan shows as active,
and ads (if enabled) disappear.

## 9. Backups and restore

The daily timer (03:00) now writes `backups/collabpad-YYYY-MM-DD.db` (14 kept) **and**
mirrors `sprites/` into `backups/sprites/`. Run it once by hand:

```bash
sudo systemctl start collabpad-backup && sudo journalctl -u collabpad-backup --since "2 min ago" --no-pager
```

It prints the backup path and `sprites: N copied, 0 removed`. Copy `/var/lib/collabpad/backups`
off the server as before.

Restore (from a backup taken by the timer):

```bash
sudo systemctl stop collabpad
```

```bash
sudo bash -c 'mkdir -p /var/lib/collabpad/replaced && mv /var/lib/collabpad/collabpad.db* /var/lib/collabpad/replaced/'
```

```bash
sudo -u collabpad cp /var/lib/collabpad/backups/collabpad-YYYY-MM-DD.db /var/lib/collabpad/collabpad.db
```

```bash
sudo -u collabpad cp -rn /var/lib/collabpad/backups/sprites/. /var/lib/collabpad/sprites/
```

```bash
sudo systemctl start collabpad
```

## 10. Roll back to the old app

The old database is untouched in `/var/lib/collabpad/legacy/`.

```bash
sudo systemctl stop collabpad
```

```bash
sudo bash -c 'mkdir -p /var/lib/collabpad/living-canvas && mv /var/lib/collabpad/collabpad.db* /var/lib/collabpad/sprites /var/lib/collabpad/living-canvas/ && cp -p /var/lib/collabpad/legacy/collabpad.db /var/lib/collabpad/'
```

```bash
cd /opt/collabpad && sudo -u collabpad git checkout legacy-v1 && sudo -u collabpad .venv/bin/pip install -r requirements.txt
```

```bash
cd /opt/collabpad/web && sudo -u collabpad npm ci && sudo -u collabpad npm run build && sudo systemctl start collabpad
```

## 11. Later updates

Unchanged from legacy: pull, install, build, restart, health check.

```bash
sudo /opt/collabpad/deploy/update.sh
```

A restart drops every socket; clients reconnect on their own and the world resumes from
the last snapshot (at most 5 s lost). Never run more than one worker or a second copy of
the service: rooms, leases and the simulation live in one process.

## 12. Smoke test after the first deploy

In two browsers (one in RU, one in EN):

1. `https://collabpad.ru/w` opens the world; both see each other's strokes.
2. Draw a cow, lasso it, press ОЖИВИ / ALIVE: goo appears in both, then the cow (with a VLM
   key, recognised; without one, the type grid).
3. `http://colabpad.ru/` redirects to `https://collabpad.ru/?utm_medium=typo_colabpad`.
4. Sign in by e-mail (the mail arrives), buy Plus with the test card, create a private world,
   share it.
5. After a minute the journal has a `Simulation:` line with p95 well under 60 ms.

Optional, on the server, before announcing: the load test, which uses a throwaway database
in `/tmp` and never touches the real one (it takes about two minutes; stop it with Ctrl+C):

```bash
cd /opt/collabpad && sudo -u collabpad env -u PYTHONPATH .venv/bin/python scripts/loadtest.py --report /tmp/loadtest.md
```

---

## Docker variant

If you run the Compose setup instead of systemd (see `docs/DOCKER.md`), the database and
sprites live in the `collabpad_data` volume. To start with a fresh database and keep the old
one:

```bash
docker compose stop app
```

```bash
docker compose run --rm --no-deps app sh -c 'mkdir -p /var/lib/collabpad/legacy && mv /var/lib/collabpad/collabpad.db* /var/lib/collabpad/legacy/'
```

```bash
docker compose up --build -d --wait
```

Settings go in `.env` next to `compose.yaml` (see `.env.example`). Backups:
`docker compose exec app python deploy/backup.py` (database and sprites, inside the volume),
then `docker compose cp app:/var/lib/collabpad/backups ./backups`. The container's `init`
sends stop signals to Uvicorn only, so the simulation saves the world on shutdown.
