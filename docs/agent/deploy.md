# Deploying CollabPad (Docker Compose + the host's Nginx)

For the human. The agent never runs any of this against the server. Every command is yours.

The layout:

- The code is a git clone in `/opt/collabpad`, and its settings are in `/opt/collabpad/.env` (mode 600).
- Docker Compose (project `collabpad`) runs:
  - `app`: the server, the built frontend and the world simulation, in one container with one Uvicorn worker. It is published on **`127.0.0.1:1744`** only.
  - `vlm-egress` (optional, profile `egress`): Tailscale in userspace mode. It serves an HTTP proxy that only the VLM calls use, leaving through a Tailscale exit node.
- The database (`collabpad.db`), the sprites and the backups are in the Docker volume `collabpad_data`.
- The **host's** Nginx terminates HTTPS (certbot, webroot) and proxies to `127.0.0.1:1744`. The VPS hosts other sites too: never remove or edit their Nginx files, and don't touch the `default` site.
- The daily backup is a root cron line that runs `deploy/backup.py` inside the container.

The host needs only Docker (Engine + the Compose plugin), Nginx, certbot and git. No Python
or Node: the image builds the frontend and runs the client tests during `docker compose build`.

---

## 1. Accounts and keys to have ready

| What | `.env` | Without it |
|---|---|---|
| SMTP mailbox for sign-in links (implicit TLS, port 465) | `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_USER`, `SMTP_PASSWORD` | sign-in links are written to the container log instead of being mailed. **Don't run production like that**: anyone who can read the log can sign in as anyone. |
| OpenRouter key | `VLM_API_KEY` | ALIVE still works: the author picks the type from a grid. Daily spend cap: `daily_budget_usd` in `config/vlm.yaml`. |
| YooKassa **test** shop: shop id and a secret key that starts with `test_` | `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY` | checkout answers "Payment failed". A key not starting with `test_` is refused on purpose. |
| Yandex Advertising Network block ids `R-A-…` | `ADS_BLOCK_SIDE`, `ADS_BLOCK_BOTTOM` | no ads. Ads also need `ads.enabled: true` in `config/flags.yaml`. |
| Tailscale (optional): an exit node, plus a **reusable, tagged** auth key whose tag may use exit nodes (`autogroup:internet`) | `TS_AUTHKEY`, `TS_EXIT_NODE` | the VLM is called directly from the server's IP |

## 2. Install the host packages

Docker (skip if it's already there):

```bash
curl -fsSL https://get.docker.com | sudo sh
```

```bash
sudo apt install -y nginx certbot git
```

## 3. Get the code

```bash
sudo git clone https://github.com/cyberpsychozz/colabpad.git /opt/collabpad
```

## 4. Settings

```bash
cd /opt/collabpad && sudo install -m 600 .env.example .env && sudo nano .env
```

Set at least:

```
COLLABPAD_PORT=1744
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
```

With the Tailscale VLM proxy, also set the following. `COMPOSE_PROFILES` makes every plain
`docker compose` command include the proxy container:

```
COMPOSE_PROFILES=egress
VLM_PROXY_URL=http://vlm-egress:1055
TS_AUTHKEY=tskey-auth-...
TS_EXIT_NODE=<exit node Tailscale IP>
```

Check that port 1744 is free (no output means free). If it's taken, pick another port for
`COLLABPAD_PORT` and use it in the Nginx `upstream` too (step 8):

```bash
sudo ss -ltnp 'sport = :1744'
```

## 5. Build and start

```bash
cd /opt/collabpad && sudo docker compose up --build -d --wait
```

The first build takes a few minutes. Check (should print `{"ok":true}`):

```bash
curl -fsS http://127.0.0.1:1744/api/health; echo
```

```bash
sudo docker compose ps
```

With the proxy, check that it leaves through the exit node (should print the exit node's
public IP, not the server's):

```bash
sudo docker compose exec app python -c "import httpx; print(httpx.get('https://ifconfig.me', proxy='http://vlm-egress:1055').text)"
```

The app ignores `HTTPS_PROXY`/`ALL_PROXY` on purpose, so only the VLM ever uses the proxy,
never YooKassa or mail. If the exit node is down, ALIVE falls back to the manual type grid.

## 6. Trust Nginx's forwarded addresses

To the app, the host's Nginx shows up as the Docker network's gateway address. The app has
to trust `X-Forwarded-For` from that address. Otherwise the YooKassa webhook allowlist sees
the gateway instead of YooKassa and rejects payment notifications. Get the gateway:

```bash
sudo docker network inspect collabpad_default --format '{{(index .IPAM.Config 0).Gateway}}'
```

Put it in `.env` (for example `COLLABPAD_FORWARDED_ALLOW_IPS=172.20.0.1`), then:

```bash
cd /opt/collabpad && sudo docker compose up -d --wait
```

## 7. Daily backup

`deploy/backup.py` takes an online SQLite backup, checks it, keeps 14 daily copies and mirrors
`sprites/` into `backups/sprites/`, all inside the volume. Open root's crontab:

```bash
sudo crontab -e
```

and add:

```
0 3 * * * cd /opt/collabpad && docker compose exec -T app python deploy/backup.py
```

Run it once by hand to check it (prints the backup path and the sprite count):

```bash
cd /opt/collabpad && sudo docker compose exec -T app python deploy/backup.py
```

To copy the backups out of the volume (for example before taking them off the server):

```bash
cd /opt/collabpad && sudo docker compose cp app:/var/lib/collabpad/backups ./backups
```

## 8. DNS, Nginx and HTTPS

Point the A records of `collabpad.ru`, `www.collabpad.ru`, `colabpad.ru` and
`www.colabpad.ru` at the server, and remove AAAA records that point anywhere else. Wait until
`dig +short collabpad.ru` (on your own machine) shows the server's IP.

First an HTTP-only site, so certbot can prove the domains. Don't touch the other sites:

```bash
sudo mkdir -p /var/www/certbot
```

```bash
sudo cp /opt/collabpad/deploy/nginx/bootstrap.conf /etc/nginx/sites-available/collabpad.conf && sudo ln -sf /etc/nginx/sites-available/collabpad.conf /etc/nginx/sites-enabled/collabpad.conf && sudo nginx -t && sudo systemctl reload nginx
```

```bash
sudo certbot certonly --webroot -w /var/www/certbot --cert-name collabpad.ru -d collabpad.ru -d www.collabpad.ru -d colabpad.ru -d www.colabpad.ru
```

Then the full site (HTTPS, WebSocket upgrade on `/socket.io/`, the `colabpad.ru` typo
redirect with `utm_medium=typo_colabpad`). Its `map` and `upstream` names start with
`collabpad_`, so they can't clash with the other sites:

```bash
sudo cp /opt/collabpad/deploy/nginx/collabpad.conf /etc/nginx/sites-available/collabpad.conf && sudo nginx -t && sudo systemctl reload nginx
```

Reload Nginx after each renewal, and test renewal:

```bash
sudo install -m 755 /opt/collabpad/deploy/renew-hook.sh /etc/letsencrypt/renewal-hooks/deploy/collabpad && sudo certbot renew --dry-run --cert-name collabpad.ru
```

Check:

```bash
curl -fsS https://collabpad.ru/api/health; echo
```

```bash
curl -sI http://colabpad.ru/ | grep -i location
```

The second should show `https://collabpad.ru/?utm_medium=typo_colabpad`.

## 9. YooKassa test shop

In the YooKassa dashboard of the **test** shop → Integration → HTTP notifications:

- URL: `https://collabpad.ru/api/billing/webhook`
- Events: `payment.succeeded`, `payment.canceled`

The app accepts notifications only from YooKassa's addresses (`webhook_ips` in
`config/billing.yaml`), which is why step 6 matters. It never trusts the notification body:
it fetches the payment from YooKassa by id first.

Check: sign in, open `/plans`, pay with a YooKassa test card for a successful payment (their
"Testing" docs list them). You come back to `/billing/return`, and the plan shows as active.

## 10. Flags

`config/flags.yaml` is built into the image:

```yaml
ads: {enabled: false}         # true: ad banners and the ad-blocker gate for free plans
billing: {recurring: false}   # true: the 03:00 MSK auto-renewal charges saved cards
vlm: {enabled: true}          # false: always the manual type grid
sleep_catchup: true
```

Change flags in the repository, then update (step 11). Don't edit files in `/opt/collabpad`
on the server: a local edit makes the next `git pull --ff-only` fail. Turn `ads.enabled` on
only after the ad block ids are set and approved.

## 11. Updates

```bash
sudo /opt/collabpad/deploy/update.sh
```

It runs `git pull --ff-only`, rebuilds, recreates the containers and checks health. A
restart drops every socket. Clients reconnect on their own, and the world resumes from the
last snapshot (at most 5 s lost). Never run a second replica of `app`.

If an update changes `deploy/nginx/collabpad.conf`, copy it again and reload Nginx:

```bash
sudo cp /opt/collabpad/deploy/nginx/collabpad.conf /etc/nginx/sites-available/collabpad.conf && sudo nginx -t && sudo systemctl reload nginx
```

## 12. Logs and restarts

```bash
cd /opt/collabpad && sudo docker compose logs --tail=200 app vlm-egress
```

```bash
cd /opt/collabpad && sudo docker compose restart app
```

About a minute after the first visitor opens the world, the app log shows a line like
`Simulation: 10 Hz, … tick p50 … ms`, and then one every minute. "running at 5 Hz" means the
server is too slow for 10 ticks a second.

## 13. Restore a backup

```bash
cd /opt/collabpad && sudo docker compose stop app
```

Replace `YYYY-MM-DD` with the backup's date. The current database is moved aside into
`replaced/`, not deleted:

```bash
cd /opt/collabpad && sudo docker compose run --rm --no-deps app sh -c 'set -e; mkdir -p /var/lib/collabpad/replaced; mv /var/lib/collabpad/collabpad.db* /var/lib/collabpad/replaced/; cp /var/lib/collabpad/backups/collabpad-YYYY-MM-DD.db /var/lib/collabpad/collabpad.db; cp -rn /var/lib/collabpad/backups/sprites/. /var/lib/collabpad/sprites/'
```

```bash
cd /opt/collabpad && sudo docker compose up -d --wait
```

## 14. Wipe the world (start over with an empty database)

This **deletes** the database, the sprites and the backups in the volume. Copy the backups out
first (step 7) if you might want them.

```bash
cd /opt/collabpad && sudo docker compose down
```

```bash
sudo docker volume rm collabpad_data
```

```bash
cd /opt/collabpad && sudo docker compose up -d --wait
```

## 15. Smoke test

In two browsers (one in RU, one in EN):

1. `https://collabpad.ru/w` opens the world, and each browser sees the other's strokes.
2. Draw a cow, lasso it and press ОЖИВИ / ALIVE. Goo appears in both, then the cow: recognised
   with a VLM key, or the type grid without one.
3. `http://colabpad.ru/` redirects to `https://collabpad.ru/?utm_medium=typo_colabpad`.
4. Sign in by e-mail (the mail arrives), buy Plus with the test card, create a private world and
   share it.

## 16. Remove CollabPad from a server

This deletes everything, including the data volume. Other sites aren't touched.

```bash
cd /opt/collabpad && sudo docker compose --profile egress down -v --rmi local
```

```bash
sudo rm -f /etc/nginx/sites-enabled/collabpad.conf /etc/nginx/sites-available/collabpad.conf && sudo nginx -t && sudo systemctl reload nginx
```

```bash
sudo certbot delete --cert-name collabpad.ru && sudo rm -f /etc/letsencrypt/renewal-hooks/deploy/collabpad
```

```bash
sudo rm -rf /opt/collabpad
```

Finally, remove the backup line from `sudo crontab -e`, and the `collabpad-vlm-egress`
machine from the Tailscale admin console.
