# Step 0 notes (T0)

Rule used to keep T0 small: **T0 moves and deletes; it does not change the behaviour of the kept
pieces.** New contracts (chunks, fates, new error codes, i18n, new schema) land in T1–T3/T24.

## Kept (exact files and functions)

| Legacy | Kept | Home now |
|---|---|---|
| `web/src/draw.js` | `simplify`, `preparePoints`, `splitStroke`, `renderStroke`, `pointerPoint`, `configureCanvas`, `fillBackground` (+ private `distanceToSegmentSquared`); all 5 tests in `draw.test.js` | unchanged |
| `web/src/socket.js` | `CanvasSync`: `outbox`, `ack` (5 s timeout), `epoch` guard, `resync`, `enqueue`, `flush`, `confirm`, 1/2/4 s backoff; 5 tests | same file; presence, name and `canvas:full` handling removed |
| `web/src/api.js` | `request`, `ApiError`, `getCanvas`, `getStrokes`, `countMetric` | same file (`createCanvas` removed) |
| `web/src/ui.js` | `$`, `storage` (localStorage in try/catch), `toast`, `dialog` | `web/src/ui/dom.js` |
| `web/src/analytics.js`, Umami tags in `web/index.html` | all | unchanged |
| `web/src/styles.css` | whole file, as the design reference | unchanged; prune as the new UI replaces it |
| `app/repo.py` | `insert_stroke` (BEGIN IMMEDIATE, dedupe, seq, rollback), `list_strokes`, `_stroke` | `app/repo/strokes.py` |
| `app/repo.py` | `create_canvas`, `get_canvas` | `app/repo/canvases.py` |
| `app/repo.py` | `increment_metric` | `app/repo/metrics.py` |
| `app/repo.py` | `Repository` | `app/repo/__init__.py`, composed from the three modules |
| `app/realtime.py` | `SlidingWindow` | `app/realtime/ratelimit.py` |
| `app/realtime.py` | `Realtime`: `sio` setup (`async_handlers=False`), `write_lock`, `check_size`, `join`, `add`, `disconnect` | `app/realtime/gateway.py`; presence and room cap removed |
| `app/schema.sql` | `stroke`, `canvas`, `metric` tables; `stroke_no_update` / `stroke_no_delete` triggers | same file; `author_name` dropped, full rewrite is T2 |
| `app/errors.py` | `AppError`, `validation_error`, `not_found`, `too_large` | unchanged |
| `app/db.py` | `connect` (WAL, FK, busy_timeout, one connection) | unchanged |
| `app/config.py` | `Settings` and its validators | same file; dead settings removed |
| `app/models.py` | `WireModel`, `JoinIn`, `StrokeIn`, `StrokeOut`, `StrokesOut`, `CanvasOut`, `ErrorOut`, `ErrorDetail`, `check_id`, `Number`, `Point` | same file |
| `app/main.py` | `PayloadLimit`, `create_app`, `create_asgi`, security headers, exception handlers, `/api/health`, `GET /api/canvases/{id}` (+ `/strokes`), `/api/metrics/{key}`, static + SPA index | same file |
| `app/__main__.py` | all | unchanged |
| Infra | `Dockerfile`, `compose.yaml`, `.dockerignore`, `.env.example`, `start.sh`, `LOCAL.md`, `docs/DOCKER.md`, all of `deploy/` | only dead env vars removed; real adaptation is T1/T27 |
| Docs | the two new docx files (names with spaces), `AGENTS.md`, `CLAUDE.md` | unchanged |

## Deleted

- **Client:** `web/src/timelapse.js`; from `ui.js`: `askName`, `copyLink`, `fakeDoor`,
  `downloadPNG`; from `main.js`: the legacy landing, canvas page, error page and `/c/:id` router
  (the file is now a brand-only placeholder shell); `web/scripts/realtime-smoke.mjs` (T25's load
  test replaces it).
- **Server:** `POST /api/canvases` and its rate limit; presence (`names`, `presence`,
  `presence:update`, `Member.name`); `ROOM_FULL`, `CANVAS_FULL`, `canvas:full`; `JoinIn.name`,
  `CanvasCreated`; `stroke.author_name`; metric keys `pro-click`, `stroke-buy-click` (the
  whitelist now holds the §9 keys `adblock_gate_shown`, `adblock_gate_passed`, `plus_clicked`).
- **Settings / env** (`config.py`, `.env.example`, `compose.yaml`, `deploy/collabpad.env.example`,
  the README table): `MAX_CLIENTS_PER_CANVAS`, `MAX_STROKES_PER_CANVAS`, `NAME_MAX_LEN`,
  `CANVAS_CREATE_LIMIT`.
- **Docs:** `docs/user-guide/`, `docs/CollabPad_Архитектура_и_ТЗ.docx` (underscores),
  `docs/spec-source.txt`, `docs/STATUS.md`, `docs/previews/`, and the links to them in `README.md`
  and `LOCAL.md`.
- **Tests** for the deleted features (canvas creation, canvas-full, room-full, presence, name
  trimming, legacy metric keys, the 5 000-stroke cap).

## Added

- Empty §2 packages (docstring-only `__init__.py`): `app/alive`, `app/sim`, `app/sim/systems`,
  `app/sim/types`, `app/auth`, `app/billing`, `app/ads`, `app/i18n`. No placeholder modules, no
  `config/` or `prompts/` files yet (T1/T6).
- `/w` serves the SPA index; `/c/{id}` redirects to `/w` (§10).
- Tests split by area: `tests/test_strokes.py`, `test_realtime.py`, `test_http.py`,
  `test_integration.py`.

## Where §0.1 looks wrong or incomplete

Items 1–3 need a decision from the human before T2/T3.

1. **"Resync from the contiguous `seq` prefix" cannot be kept per chunk.** `seq` is per canvas, so
   the seqs inside one chunk are sparse and a gap is undetectable from the client. Proposal for T3:
   each chunk keeps a watermark = the `lastSeq` the server returned on its last fetch; reconnect
   refetches `afterSeq=watermark`; live events above it are applied idempotently.
2. **`chunk_x/chunk_y` from the stroke's first point (§5) loses strokes that cross chunks.** A
   viewer subscribed to chunk B never receives a stroke that starts in A and runs into B, and the
   ALIVE selection ("chunks overlapped by the bbox") can miss it too. Cheapest fix: the kept
   `splitStroke` also cuts at chunk borders, so every stored stroke lies in one chunk.
3. **Canvas width/height is a rename, not a deletion.** `canvas.width/height` and
   `CANVAS_WIDTH/HEIGHT` bound point validation; they become `bounds_w/bounds_h` in T2 and were
   kept through T0 so validation and its tests survive. Neither the brief nor the TZ gives the
   size of the shared world.
4. **Kept things §0.1 doesn't list:** `PayloadLimit` (produces `PAYLOAD_TOO_LARGE` 413 for REST and
   oversized WS packets), the security-headers middleware, the `ui.js` helpers, `api.js`, the
   `metric` table, the `deploy/backup.py` test.
5. **`README.md` is not mentioned anywhere.** It still describes the legacy product and holds the
   VPS install order that `LOCAL.md` points to. T0 only removed dead links and env rows. Suggest
   rewriting it in T1 and moving the VPS steps into `docs/agent/deploy.md` in T27. `LOCAL.md`'s
   manual-check paragraph also still describes the legacy flow.
6. **Error codes change name, not just shape:** `VALIDATION_ERROR` → `VALIDATION_FAILED`,
   `CANVAS_NOT_FOUND` → `NOT_FOUND`; `INVALID_ID` and `NOT_JOINED` have no §8 equivalent. Left
   as-is in T0; rename together with the envelope extension.
7. **Russian string literals in code** (`errors.py`, `main.py`, `realtime/gateway.py`, `draw.js`,
   `api.js`, the `<title>` and description in `index.html`) break ground rule 9 until T24 moves
   them into catalogs. T0 added none.
8. **`SlidingWindow` is keyed by socket id**, so a reconnect resets the stroke limit. T23 should
   key it by session.
9. **Layout additions to §2:** `app/repo/metrics.py`, `web/src/ui/dom.js`, `web/src/api.js`.
   `npm test` globs `src/*.test.js`; it must become recursive once tests live in subfolders.
10. **One pytest case needs a built frontend.** `GET /assets/missing.js` raises instead of
    returning 404 when `app/static/assets` doesn't exist (legacy behaviour, `StaticFiles` with
    `check_dir=False`). Run `npm run build` before `pytest`, or make the mount conditional in T1.

## State after T0

- The server boots, `/api/health` is ok, and strokes can be inserted and read through the repo and
  the socket (`canvas:join` → `stroke:add`), as the kept tests exercise them.
- There is no REST way to create a canvas (tests use `repo.create_canvas`; T2 creates the shared
  world at startup).
- The frontend builds and shows a brand-only placeholder at `/` and `/w`. **Drawing in the browser
  is gone until T3**, so no two-client UI check applies to T0.
