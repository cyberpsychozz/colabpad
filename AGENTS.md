# AGENTS.md — CollabPad

Instructions for coding agents working on this repository. Read this file fully before writing code.
The human-facing overview is `README.md` (Russian). The full architecture and spec is
`docs/CollabPad_Архитектура_и_ТЗ.docx` (Russian). If this file and the docx disagree, **this file wins**
(it is newer), and flag the conflict to the human.

**User-approved update (2026-11-09):** Docker packaging is now in scope.
Use Dockerfile and compose.yaml for container builds and local startup; see
docs/DOCKER.md. The bare-VPS setup in §10 remains an alternative. Run exactly
one app container with one Uvicorn worker and persist SQLite in its named volume.

---

## 1. What we are building

CollabPad is a browser drawing canvas with **no undo and no eraser**. Every stroke is permanent, like ink on paper.
Anyone with a canvas link can draw on it together in real time. No accounts, no sign-up.

MVP = exactly three features:

1. **Real-time canvas without undo.** Brush with color + width; mouse, touch, stylus. Strokes sync live between everyone on the canvas. Strokes are immutable.
2. **Canvas by link.** One button creates a canvas and returns a unique URL `/c/{id}`. Anyone with the link can draw. Reopening the link restores the canvas.
3. **Timelapse + PNG.** Replay the canvas stroke by stroke in the browser; download the current canvas as PNG.

Plus one tiny measurement feature: **fake-door buttons** that only count clicks (see §6.4).

Target: one developer, 7 days, ~50 hours.

---

## 2. Hard invariants (never break these)

1. **No undo, no eraser, no stroke edits, no stroke deletes.** Not in the UI, not in the API, not in the repository layer. The `stroke` table is append-only and protected by DB triggers (§5). Do not add "practice mode", "admin delete", "clear canvas" or anything similar. This was explicitly decided by the product owner.
2. **Exactly one Uvicorn worker.** Rooms, presence and rate-limit state live in process memory, and stroke ordering relies on a single event loop. Never set `--workers` > 1, never add Gunicorn with multiple workers.
3. **No `await` inside a DB transaction.** The seq-assignment transaction must run synchronously start-to-finish so two strokes can't interleave.
4. **Server assigns `seq`.** Clients never choose stroke order. Render order is always ascending `seq`.
5. **Idempotent stroke submission.** `(canvas_id, client_id)` is unique; resubmitting the same `clientId` returns the already-assigned `seq` instead of inserting twice.
6. **No personal data.** Do not store IPs, user agents, or emails. The display name is optional, max 30 chars, stored only on strokes.
7. **User text is text.** Never inject names or any user-supplied string as HTML (`textContent`, not `innerHTML`).

---

## 3. Out of scope (do not implement)

Accounts / login · personal / invite-only / open canvas types (only "canvas by link" exists) · view-only links, canvas passwords · public canvases, moderation, subscriptions · eraser, layers, extra brushes · undo in any form · timelapse video export · real payments (Pro subscription or pay-per-stroke) · mobile app, Telegram Mini App.

If a task seems to need one of these, stop and ask the human.

---

## 4. Stack

| Layer | Choice |
|---|---|
| Server | Python 3.12, **FastAPI**, **python-socketio** (ASGI, mounted alongside FastAPI), Uvicorn (1 worker) |
| Validation / config | Pydantic v2, pydantic-settings |
| DB | SQLite via stdlib `sqlite3`, WAL mode, no ORM |
| IDs | `nanoid` (Python package), size 10, default alphabet `A-Za-z0-9_-` |
| Client | Vanilla JS + HTML5 Canvas, built with **Vite**, `socket.io-client` v4 |
| Tests | `pytest`, `httpx` / FastAPI `TestClient` |
| Prod | Ubuntu VPS, system-wide Nginx, Docker Compose or systemd, Let's Encrypt (certbot) |

`requirements.txt` (minimums; pin exact versions once it works):

```
fastapi>=0.115
uvicorn[standard]>=0.30
python-socketio>=5.11
pydantic>=2.7
pydantic-settings>=2.4
nanoid>=2.0
pytest>=8
httpx>=0.27
```

---

## 5. Repository layout

```
collabpad/
  app/
    __init__.py
    main.py         # FastAPI app, REST routes, error handlers, static + SPA fallback, builds `asgi`
    realtime.py     # Socket.IO server + event handlers (join, stroke:add, disconnect), presence, rate limits
    repo.py         # all SQL; only INSERT/SELECT on stroke
    db.py           # connection, PRAGMAs, schema init
    models.py       # Pydantic models: StrokeIn, StrokeOut, CanvasOut, ErrorOut
    errors.py       # AppError(code, message, field, http_status) + helpers
    config.py       # Settings (pydantic-settings)
    schema.sql      # tables + triggers (below)
    static/         # Vite build output (gitignored)
  web/
    index.html
    vite.config.js  # build.outDir = '../app/static'; dev proxy for /api and /socket.io (ws: true) -> :8000
    src/
      main.js       # routing: "/" home, "/c/:id" canvas
      api.js        # REST calls
      socket.js     # socket.io-client wrapper, outbox, reconnect + resync
      draw.js       # pointer capture, smoothing, RDP simplification, renderer (shared by live/load/timelapse)
      timelapse.js
      ui.js         # toasts, banners, states, name prompt
      styles.css
  tests/
  deploy/
    nginx/collabpad.conf
    systemd/collabpad.service
    collabpad.env.example
    update.sh
  docs/
    CollabPad_Архитектура_и_ТЗ.docx
  requirements.txt
  README.md
  AGENTS.md
```

### `app/schema.sql`

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS canvas (
  id            TEXT PRIMARY KEY,                 -- nanoid(10)
  width         INTEGER NOT NULL DEFAULT 1600,
  height        INTEGER NOT NULL DEFAULT 1000,
  background    TEXT    NOT NULL DEFAULT '#FFFFFF',
  stroke_count  INTEGER NOT NULL DEFAULT 0,       -- equals the last assigned seq
  created_at    INTEGER NOT NULL                  -- unix ms
);

CREATE TABLE IF NOT EXISTS stroke (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  canvas_id    TEXT    NOT NULL REFERENCES canvas(id),
  seq          INTEGER NOT NULL,
  client_id    TEXT    NOT NULL,                  -- UUID from client, for dedupe
  author_name  TEXT,
  color        TEXT    NOT NULL,                  -- #RRGGBB
  width        REAL    NOT NULL,                  -- 1..40
  points       TEXT    NOT NULL,                  -- JSON [[x,y,p], ...]
  created_at   INTEGER NOT NULL,
  UNIQUE (canvas_id, seq),
  UNIQUE (canvas_id, client_id)
);

CREATE TABLE IF NOT EXISTS metric (
  key    TEXT PRIMARY KEY,
  value  INTEGER NOT NULL DEFAULT 0
);

CREATE TRIGGER IF NOT EXISTS stroke_no_update BEFORE UPDATE ON stroke
  BEGIN SELECT RAISE(ABORT, 'strokes are immutable'); END;
CREATE TRIGGER IF NOT EXISTS stroke_no_delete BEFORE DELETE ON stroke
  BEGIN SELECT RAISE(ABORT, 'strokes are immutable'); END;
```

Connection setup (`db.py`): `sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)`, then
`PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000; PRAGMA synchronous=NORMAL;`.
Use explicit `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK`. One shared connection for the process is fine.

---

## 6. Contracts

JSON uses **camelCase** on the wire (use a Pydantic alias generator), snake_case in Python and SQL.

### 6.1 Error format (REST body and Socket.IO ack)

```json
{"ok": false, "error": {"code": "VALIDATION_ERROR", "message": "human readable (Russian)", "field": "color"}}
```

| code | HTTP | when |
|---|---|---|
| `VALIDATION_ERROR` | 400 | bad payload (map Pydantic `RequestValidationError` and model errors to this; set `field`) |
| `INVALID_ID` | 400 | id doesn't match `^[A-Za-z0-9_-]{10}$` |
| `CANVAS_NOT_FOUND` | 404 | no such canvas |
| `NOT_JOINED` | — | `stroke:add` before `canvas:join`, or for a different canvas than joined |
| `CANVAS_FULL` | — | canvas reached `MAX_STROKES_PER_CANVAS` |
| `ROOM_FULL` | — | canvas already has `MAX_CLIENTS_PER_CANVAS` connections |
| `RATE_LIMITED` | 429 | rate limit exceeded |
| `PAYLOAD_TOO_LARGE` | 413 | message > 64 KB |
| `INTERNAL` | 500 | anything unexpected (log the traceback, don't leak it) |

### 6.2 REST

| Method | Response | Errors |
|---|---|---|
| `POST /api/canvases` (empty body) | `201 {"id","url":"/c/{id}","width","height"}` | 429 (> `CANVAS_CREATE_LIMIT` per client IP), 500 |
| `GET /api/canvases/{id}` | `200 {"id","width","height","background","strokeCount","maxStrokes","createdAt","palette":[...],"brushWidthMin","brushWidthMax","strokes":[Stroke]}` | 400 `INVALID_ID`, 404 |
| `GET /api/canvases/{id}/strokes?afterSeq=N` | `200 {"strokes":[Stroke]}` with `seq > N`, ascending | 400, 404 |
| `POST /api/metrics/{key}` | `204` | 404 if key not in whitelist `pro-click`, `stroke-buy-click` |
| `GET /c/{id}` | `index.html` (SPA; client shows 404 screen itself) | — |
| `GET /api/health` | `200 {"ok":true}` | — |

Client IP for rate limiting comes from `X-Forwarded-For` via Uvicorn `--proxy-headers --forwarded-allow-ips 127.0.0.1`. Use it only in memory; never persist it.

`Stroke` object:

```json
{"seq": 215, "authorName": "Аня", "color": "#1E88E5", "width": 6,
 "points": [[120.5, 340.1, 0.5], [122.0, 342.7, 0.6]], "createdAt": 1793628000000}
```

### 6.3 Socket.IO (default namespace `/`, path `/socket.io/`)

| Event | Dir | Payload | Ack / effect |
|---|---|---|---|
| `canvas:join` | C→S | `{canvasId, name?}` | ack `{ok:true, online:[names], lastSeq}`; emit `presence:update` to room |
| `stroke:add` | C→S | `{canvasId, clientId, color, width, points}` | ack `{ok:true, seq}`; emit `stroke:new` to room **skipping sender** |
| `stroke:new` | S→C | `Stroke` | client draws it |
| `presence:update` | S→C | `{online:[names]}` | update online panel; unnamed users show as "Гость" |
| `canvas:full` | S→C | `{maxStrokes}` | client switches to view-only |

In python-socketio the handler's return value is the ack. Server setup:
`socketio.AsyncServer(async_mode="asgi", cors_allowed_origins=settings.cors_origins, max_http_buffer_size=settings.max_payload_bytes)`,
and `asgi = socketio.ASGIApp(sio, other_asgi_app=app)`. Run `uvicorn app.main:asgi`.

### 6.4 Validation rules (`StrokeIn`)

- `canvasId`: matches id regex and equals the canvas this socket joined (else `NOT_JOINED`)
- `clientId`: UUID string
- `color`: `^#[0-9A-Fa-f]{6}$`
- `width`: number, `BRUSH_WIDTH_MIN..BRUSH_WIDTH_MAX` (1..40)
- `points`: list of 2..`MAX_POINTS_PER_STROKE` (2000) items; each `[x, y, p]` with `0 ≤ x ≤ width`, `0 ≤ y ≤ height`, `0 ≤ p ≤ 1`, all finite numbers
- `name` (join): optional, trimmed, cut to 30 chars, empty → null
- Rate limit: ≤ 30 `stroke:add` per 10 s per socket (sliding window, in memory)

### 6.5 Stroke insert (repo.py) — the critical path

```
BEGIN IMMEDIATE
  existing = SELECT seq FROM stroke WHERE canvas_id=? AND client_id=?
  if existing: COMMIT; return existing          # idempotent resend
  count = SELECT stroke_count FROM canvas WHERE id=?
  if count is None: ROLLBACK; raise CANVAS_NOT_FOUND
  if count >= MAX_STROKES: ROLLBACK; raise CANVAS_FULL
  INSERT INTO stroke (..., seq=count+1, ...)
  UPDATE canvas SET stroke_count = stroke_count + 1 WHERE id=?
COMMIT
```

After commit (not inside): `await sio.emit("stroke:new", stroke, room=canvas_id, skip_sid=sid)`.
When `count + 1 == MAX_STROKES`, also emit `canvas:full` to the room.

Only `canvas.stroke_count` and `metric.value` are ever updated. Nothing is ever deleted.

---

## 7. Client behavior

**Coordinates.** Canvas is fixed at 1600×1000 logical px. Convert pointer coords from CSS pixels to logical coords; scale the backing store by `devicePixelRatio` for sharp lines. The canvas element scales to fit the viewport keeping aspect ratio.

**Capturing a stroke.** `pointerdown` → `setPointerCapture`; on `pointermove` use `getCoalescedEvents()`; pressure = `event.pressure`, but if the pointer is a mouse or pressure is 0, use 0.5. Drop points closer than 1 px to the previous one. Draw locally immediately.
On `pointerup`: simplify with Ramer–Douglas–Peucker (ε = `SIMPLIFY_EPSILON`, 0.5 px), round to 0.1 px. A single click becomes two identical points. If > 2000 points, split into several strokes overlapping by 1 point. Assign `clientId = crypto.randomUUID()` and push into the outbox.

**Rendering (one function for live, load and timelapse).** Quadratic curves through segment midpoints; `lineCap = lineJoin = "round"`; segment width = `width * (0.5 + pressure)` (mouse → exactly `width`).

**Outbox & reconnect.** Outbox is an in-memory FIFO. Send with ack + 5 s timeout. On ack `ok` → remove. On `VALIDATION_ERROR` → drop the stroke, remove it from screen (redraw from confirmed strokes), toast. On `RATE_LIMITED` → wait 2 s, retry. On `CANVAS_FULL` → drop, view-only. On `INTERNAL`/timeout → retry with backoff 1, 2, 4 s, then show "Проблемы с сервером".
On socket `connect` (including reconnects): `canvas:join` → `GET /strokes?afterSeq=lastSeq` → draw missing → flush outbox.
Keep `lastSeq` = max seq seen. During a session local strokes may sit above or below others in a slightly different order than seq; that's accepted. After reload, order is exactly by seq.

**Timelapse.** Separate overlay canvas. Replay confirmed strokes by seq, whole strokes (no intra-stroke animation). At 1×: 40 ms per stroke, but total ≤ 60 s (shrink per-stroke time if needed). Controls: play/pause, 1×/2×/5×, scrubber by stroke index, PNG, close. Empty canvas → "Пока нечего показывать — нарисуй первый штрих", play disabled.

**PNG.** `canvas.toBlob("image/png")` of the main canvas at logical 1600×1000 with white background, filename `collabpad-{id}.png`.

**Name.** On first visit to any canvas: modal "Как тебя подписать?" with skip. Store in `localStorage` (wrap in try/catch).

**First-visit hint.** "Здесь нет отмены. Каждый штрих — навсегда." once (localStorage flag).

### UI states (all user-facing text in Russian)

| State | Where | Show |
|---|---|---|
| Loading | open canvas | gray canvas + "Загружаем холст…"; after 5 s add "Сервер отвечает медленно, подожди немного" |
| Loading | create | button disabled, "Создаём…" |
| Connection | canvas panel | 🟢 "Подключено" · 🟡 "Сохранение… (N)" when outbox non-empty · 🔴 "Нет связи — штрихи сохранятся после переподключения" |
| Success | copy link | toast "Ссылка скопирована" (2 s) |
| Success | PNG | toast "Картинка сохранена" |
| Error | not found | "Такого холста нет. Проверь ссылку или создай новый." + "Создать холст" |
| Error | full | banner "Холст заполнен — здесь уже 5 000 штрихов. Можно посмотреть таймлапс или скачать PNG."; tools disabled |
| Error | room full | view-only, banner "На холсте сейчас слишком много людей. Обнови страницу позже." |
| Error | stroke rejected | toast "Этот штрих не сохранился" |
| Empty | new canvas | centered faint text "Начни рисовать. Отменить нельзя 🙂" + "Позови друзей: [Копировать ссылку]"; hides after first stroke |

Warn on `beforeunload` if the outbox is non-empty.

**Fake-door buttons.** "Pro" button in the canvas toolbar and "Купить штрих на большом холсте" on the home page: on click → `POST /api/metrics/pro-click` / `stroke-buy-click`, then a modal "Скоро! Мы проверяем, нужно ли это". No payment code. Pay-per-stroke is an **open question** (docx §5.4) — only the counter is in scope.

---

## 8. Configuration (`app/config.py`, overridable via env / `.env`)

```
PORT=8000
DB_PATH=./data/collabpad.db          # prod: /var/lib/collabpad/collabpad.db
CORS_ORIGINS=http://localhost:5173  # prod: https://collabpad.ru
CANVAS_WIDTH=1600
CANVAS_HEIGHT=1000
MAX_STROKES_PER_CANVAS=5000
MAX_POINTS_PER_STROKE=2000
MAX_PAYLOAD_BYTES=65536
MAX_CLIENTS_PER_CANVAS=20
STROKE_RATE_LIMIT=30/10
CANVAS_CREATE_LIMIT=10/60
BRUSH_WIDTH_MIN=1
BRUSH_WIDTH_MAX=40
SIMPLIFY_EPSILON=0.5
NAME_MAX_LEN=30
PALETTE=#000000,#FFFFFF,#E53935,#FB8C00,#FDD835,#43A047,#1E88E5,#8E24AA
```

The client never hardcodes these; it reads them from `GET /api/canvases/{id}`. Create the DB directory on startup if missing and run `schema.sql` (idempotent).

---

## 9. Commands

Docker (builds the frontend and Python dependencies without host installation):

~~~bash
docker compose up --build -d --wait
docker compose down
~~~

The port is bound to host loopback. A named volume holds the DB; ordinary
container replacement and compose down preserve it. Do not scale the app service.
Local .env, data, Git and installed dependencies are excluded from build context.

```bash
# backend
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:asgi --reload --port 8000          # dev (reload is fine in dev; still 1 process)

# frontend
cd web && npm install
npm run dev        # http://localhost:5173, proxies /api and /socket.io to :8000
npm run build      # outputs to ../app/static

# tests
pytest -q
```

FastAPI must serve `app/static` and fall back to `index.html` for `/` and `/c/{id}`. Register API routes **before** mounting static files.

---

## 10. Production deploy (bare VPS alternative, system Nginx)

- **Primary domain:** `collabpad.ru`. **Typo domain:** `colabpad.ru` → `301` to `https://collabpad.ru$request_uri`. `www.` variants of both also redirect to `https://collabpad.ru`.
- DNS: A (and AAAA if available) records for `collabpad.ru`, `www.collabpad.ru`, `colabpad.ru`, `www.colabpad.ru` → VPS IP.
- Layout: code in `/opt/collabpad` owned by system user `collabpad`; venv in `/opt/collabpad/.venv`; DB in `/var/lib/collabpad/`; env file `/etc/collabpad/collabpad.env`.
- App listens on `127.0.0.1:8000` only.

### `deploy/systemd/collabpad.service`

```ini
[Unit]
Description=CollabPad (FastAPI + Socket.IO)
After=network.target

[Service]
User=collabpad
Group=collabpad
WorkingDirectory=/opt/collabpad
EnvironmentFile=/etc/collabpad/collabpad.env
ExecStart=/opt/collabpad/.venv/bin/uvicorn app.main:asgi --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
Restart=always
RestartSec=2
NoNewPrivileges=true
ProtectSystem=full
ReadWritePaths=/var/lib/collabpad

[Install]
WantedBy=multi-user.target
```

### `deploy/nginx/collabpad.conf` (→ `/etc/nginx/sites-available/`, symlink into `sites-enabled/`)

```nginx
map $http_upgrade $connection_upgrade { default upgrade; '' close; }

upstream collabpad_app { server 127.0.0.1:8000; }

# HTTP: ACME challenge + redirect everything to the primary HTTPS domain
server {
    listen 80;
    listen [::]:80;
    server_name collabpad.ru www.collabpad.ru colabpad.ru www.colabpad.ru;

    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://collabpad.ru$request_uri; }
}

# HTTPS typo + www domains -> primary
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;                      # nginx < 1.25.1: remove this line, use "listen 443 ssl http2;"
    server_name www.collabpad.ru colabpad.ru www.colabpad.ru;

    ssl_certificate     /etc/letsencrypt/live/collabpad.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/collabpad.ru/privkey.pem;

    return 301 https://collabpad.ru$request_uri;
}

# Primary
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name collabpad.ru;

    ssl_certificate     /etc/letsencrypt/live/collabpad.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/collabpad.ru/privkey.pem;

    client_max_body_size 1m;

    location /socket.io/ {
        proxy_pass http://collabpad_app;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_buffering off;
    }

    location / {
        proxy_pass http://collabpad_app;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Certificates (one cert for all four names)

Chicken-and-egg: the 443 blocks need the cert to exist. First enable only the port-80 block, then:

```bash
sudo mkdir -p /var/www/certbot
sudo certbot certonly --webroot -w /var/www/certbot \
  -d collabpad.ru -d www.collabpad.ru -d colabpad.ru -d www.colabpad.ru
sudo nginx -t && sudo systemctl reload nginx     # now with the full config
```

Renewal: certbot's systemd timer; add `--deploy-hook "systemctl reload nginx"` (or put a hook in `/etc/letsencrypt/renewal-hooks/deploy/`).

### `deploy/update.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /opt/collabpad
sudo -u collabpad git pull --ff-only
sudo -u collabpad .venv/bin/pip install -r requirements.txt
(cd web && sudo -u collabpad npm ci && sudo -u collabpad npm run build)
sudo systemctl restart collabpad      # clients auto-reconnect and resync
curl -fsS https://collabpad.ru/api/health
```

### Backups (recommended, cheap)

Daily cron as `collabpad`: `sqlite3 /var/lib/collabpad/collabpad.db ".backup '/var/lib/collabpad/backups/collabpad-$(date +\%F).db'"` and keep the last 14 files. Never copy the live `.db` file with `cp` while the app runs (WAL).

---

## 11. Work plan (do tasks in this order; critical path = T1→T2→T6→T7→T13→T14)

| ID | Task | h | Depends | Done when |
|---|---|---|---|---|
| T1 | Repo skeleton: FastAPI + python-socketio + Uvicorn, Vite, config.py, health endpoint, deployed to VPS behind Nginx with both domains | 3 | — | `https://collabpad.ru/api/health` ok; `colabpad.ru` redirects |
| T2 | schema.sql, db.py, repo.py | 3 | T1 | pytest: UPDATE/DELETE on stroke raises |
| T3 | REST: create, get, afterSeq, errors | 3 | T2 | pytest + `/docs`; error codes match §6.1 |
| T4 | Client brush: pointer events, smoothing, pressure, color/width, DPR | 6 | T1 | smooth lines with mouse, finger, stylus |
| T5 | Socket.IO join, rooms, presence, ROOM_FULL | 3 | T1 | two tabs see each other online |
| T6 | `stroke:add`: validation, rate limit, seq transaction, dedupe, broadcast, canvas:full | 5 | T2, T5 | pytest for every invalid example in §6.4 + dedupe + full |
| T7 | Client sync: outbox, acks, `stroke:new`, reconnect resync | 5 | T3, T4, T6 | Wi-Fi off 30 s while drawing → nothing lost |
| T8 | Routing `/c/:id`, load canvas, 404 screen, name modal | 3 | T3, T4 | canvas restores after reload |
| T9 | Timelapse player | 5 | T8 | 1000-stroke canvas plays smoothly |
| T10 | PNG download | 1 | T4 | PNG matches canvas |
| T11 | Home page, copy link, all UI states (§7) | 4 | T8 | every state reproducible by hand |
| T12 | Fake-door buttons + metric counters | 1 | T3 | counters increment |
| T13 | Final deploy, backups, verify DB survives redeploy | 2 | T7, T11 | data survives `update.sh` |
| T14 | Test with 5–10 people, fix bugs | 8 | all | no critical bugs; NFRs below met |

If time runs short, cut T9 down to play/pause only (no scrubber/speeds). Never cut T6/T7 robustness.

---

## 12. Non-functional targets (verify in T14)

- Remote stroke appears ≤ 300 ms p95 after pen-up (same region).
- Canvas with 5000 strokes loads ≤ 2 s on a mid-range laptop.
- 10 simultaneous users on one canvas; 50 active canvases on one VPS.
- Works on latest Chrome, Safari (macOS + iOS), Firefox, Edge.

---

## 13. Conventions

- Python: type hints everywhere, `ruff` formatting, small modules as in §5. All SQL lives in `repo.py`.
- JS: ES modules, no framework, no TypeScript required. Keep `draw.js` pure (no DOM state) so it's reusable for timelapse.
- User-facing strings: Russian. Code, comments, commits, logs: English.
- Commits: small, one task ID in the message (e.g. `T6: dedupe strokes by clientId`).
- Before marking a task done: run `pytest -q`, build the frontend, and manually test in two browser tabs.
- Ask the human before: adding a dependency not listed in §4, changing any contract in §6, touching anything in §2 or §3.
