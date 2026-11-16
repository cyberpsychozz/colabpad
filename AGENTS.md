# Agent brief: build CollabPad «Живой холст» / "Living Canvas"

You are rebuilding **collabpad.ru**, a small no-undo collaborative drawing board, into a **shared play world where drawings come alive**. You work **inside the existing repository**, on a new branch. The old code is disposable. This brief is self-contained and replaces the repo's old `AGENTS.md`.

Background documents (in Russian), copied into `docs/`:
- `docs/CollabPad Бизнес-гипотеза.docx`: why, for whom, pricing.
- `docs/CollabPad Архитектура и ТЗ.docx` (with spaces): the full TZ, with diagrams, wireframes and the task plan. Don't confuse it with the **old** `docs/CollabPad_Архитектура_и_ТЗ.docx` (with underscores), which is legacy and gets deleted.

**Precedence:** this brief is newer than the TZ and wins on the **stack** (Python + SQLite, not Node/Postgres), the **codebase approach**, the **stroke storage model**, **languages** (RU + EN), and the button text (**ОЖИВИ** / **ALIVE**). On product behaviour they agree. If anything is unclear or contradicts reality, **stop and ask the human**.

---

## 0. Ground rules (read first)

1. **Rebuild in place, and feel free to delete.**
   - The current commit is tagged `legacy-v1`. If the human ever needs the old app, they'll fork from that tag. So you **may delete or rewrite any code** that the new product doesn't need.
   - Old `/c/:id` share links may stop working. **The database may be wiped and recreated from scratch**; there is no data migration.
   - Don't keep old code "just in case". Don't shape the new design around the old one.
2. **Keep and adapt the infrastructure and workflows:** `Dockerfile`, `compose.yaml`, `.dockerignore`, `.env.example`, `start.sh`, `LOCAL.md`, `docs/DOCKER.md`, and everything in `deploy/` (Nginx with WebSocket upgrade, the `colabpad.ru` → `collabpad.ru` typo redirect with UTM, systemd units, the backup timer, `update.sh`, the certbot renew hook). Update them for the new app instead of replacing them. Keep the Umami analytics hook (`web/src/analytics.js`, the tag in `web/index.html`).
3. **Never touch production.** No ssh/scp/rsync, and never run `deploy/update.sh` or other deploy scripts. Wiping the production DB and deploying are **human steps**. Write the exact commands in `docs/agent/deploy.md`.
4. **No real money, no live keys.**
   - ЮKassa runs in **test mode** only, with credentials from env vars.
   - VLM keys come only from env vars, with a **daily budget cap**.
   - Never commit secrets; add `.env.example` entries instead.
5. **Flags** live in `config/flags.yaml`: `ads.enabled` (false), `billing.recurring` (false), `vlm.enabled` (true), `sleep_catchup` (true).
6. **The no-undo principle is sacred.** Stroke rows are **never updated or deleted**; DB triggers enforce this, as they do today. A stroke's fate (consumed, destroyed, fossil) is recorded in a separate append-only table (§5).
7. **Exactly one Uvicorn worker** (kept from legacy). The world simulation runs in **one separate process** started by the app (§2).
8. **Commits:** small, one WBS task ID each, on the branch `living-canvas`. **No AI attribution** lines in commits or PR descriptions.
9. **Bilingual from day one** (§9a): every user-facing string goes through i18n, in **Russian and English**. Code, comments, logs and identifiers are English.
10. **Ask before adding a dependency** not listed in §2.
11. **Prove each task:** `pytest -q` and `npm test` green, and for UI work, check the scenario in **two browser clients**.

### 0.1 Legacy code: what's worth keeping

| Legacy piece | Path | Verdict |
|---|---|---|
| Stroke geometry: RDP `simplify`, `preparePoints`, `splitStroke`, `renderStroke` (quadratic curves, pressure width) + tests | `web/src/draw.js`, `draw.test.js` | **Keep and adapt:** world coordinates instead of a fixed 1600×1000; `pointerPoint` goes through the camera (pan/zoom) |
| Resilient sync: outbox, ack with timeout, epoch guard, reconnect → resync from the contiguous `seq` prefix + tests | `web/src/socket.js`, `socket.test.js` | **Keep the pattern:** adapt it to chunk subscriptions (per-chunk `afterSeq`) and the new events |
| Stroke insert transaction: `BEGIN IMMEDIATE`, dedupe by `(canvas_id, client_id)`, server-assigned `seq`, no `await` inside, broadcast after commit | `app/repo.py`, `app/realtime.py` | **Keep:** add chunk columns |
| Immutability triggers on `stroke` | `app/schema.sql` | **Keep:** add the same triggers to `stroke_fate` |
| Error envelope `{ok:false, error:{code, message, field}}` | `app/errors.py` | **Keep and extend** with `retryable`, `details`, localized `message` |
| `SlidingWindow` rate limiter | `app/realtime.py` | **Keep** |
| SQLite setup (WAL, PRAGMAs, explicit transactions, one shared connection) | `app/db.py` | **Keep** |
| Settings via pydantic-settings, camelCase wire / snake_case code | `app/config.py`, `app/models.py` | **Keep the approach** |
| Visual language (colours, type, landing style) | `web/src/styles.css`, landing in `web/src/main.js` | **Design reference:** the new UI and landing are new, in RU + EN |
| Fixed-size canvas, `/c/:id` link canvases, presence names, timelapse, PNG export, fake-door buttons, `canvas.width/height`, `MAX_CLIENTS_PER_CANVAS`, `MAX_STROKES_PER_CANVAS` | various | **Delete** |
| Old user guide (`docs/user-guide/`), old TZ `docs/CollabPad_Архитектура_и_ТЗ.docx` (underscores), `docs/spec-source.txt`, `docs/STATUS.md`, old `docs/previews/` | `docs/` | **Delete.** Keep the two new docx files (names with spaces) and `docs/DOCKER.md` (adapt it). `AGENTS.md` is already this brief |

### 0.2 Step 0 (before writing features)
1. Read the repo. Write `docs/agent/step0-notes.md`: what you keep, what you delete, and anything you think §0.1 gets wrong.
2. Delete what isn't needed. Restructure into the layout in §2, keeping the kept modules **with their tests passing**.
3. Show the human the notes and the skeleton, then continue.

---

## 1. What we're building

The user draws (no undo), clicks **✨**, lassoes a drawing and presses **ОЖИВИ** (RU) / **ALIVE** (EN).
1. While the server works, the selected area is covered in animated **fairy goo** («волшебная слизь») that every viewer nearby sees.
2. A VLM sorts the drawing into one of **13 object types**, or the fallback "living scribble", and marks body parts.
3. The server builds a **procedural rig from the user's own lines** and bakes a sprite sheet.
4. The goo pops, and the object starts living in a **server-authoritative world simulation**: it eats, drinks, hunts, breeds, burns, explodes, or can be driven by remote control. All of this is synced to every viewer.

**Plans:**
- **Free:** one big shared world (bounded but huge), Yandex ad banners on both sides, canvas blocked if an adblocker is on.
- **«Плюс» / "Plus" (299 ₽/month):** no ads, up to 5 private worlds with link or account access and roles, more ALIVEs.
- **Only the host pays.** Guests are free and still see ads.

**Out of scope:** custom objects from text descriptions, PvE/PvP/«театр» modes, AI-generated sprites, timelapse/clip/PNG export, report/ban tools, ✨ packs, Telegram Mini App, multi-node scaling, A* pathfinding, legacy data migration.

---

## 2. Stack and layout

| Layer | Choice | Notes |
|---|---|---|
| Server | **Python 3.12, FastAPI, python-socketio**, Uvicorn with **1 worker** | as today |
| Simulation | a **separate Python process** (`multiprocessing`, spawn) started in the FastAPI lifespan and restarted if it dies | keeps the event loop free; the GIL makes a thread unsuitable |
| DB | **SQLite** (WAL), stdlib `sqlite3`, plain SQL in `repo.py`-style modules | as today. **The main process is the only DB writer**; the sim sends it snapshots and events |
| Raster | **skia-python** | Python port of `renderStroke` (same curves, caps and pressure width). Golden test against a browser render, within tolerance |
| Image / maths | **Pillow**, **numpy** | rig, bake, alpha trim, marching squares, vectorized sim movement |
| HTTP client | **httpx** (already pinned) | VLM, ЮKassa |
| Config | PyYAML (already pinned) + pydantic-settings | — |
| Client | **Vanilla JS** (ES modules) + Vite + Canvas 2D + socket.io-client | as today; no framework, no TypeScript |
| Tests | pytest (+ `pytest-asyncio` if needed), `node --test` for client modules; Playwright for the §12 scenarios only if the human approves the dependency | — |
| Infra | the existing Dockerfile (Node build stage + Python runtime), Compose and `deploy/`, adapted | the human deploys |

New dependencies you may add without asking: `skia-python`, `Pillow`, `numpy`, `pytest-asyncio`. **Anything else: ask first.**

```
app/
  main.py, config.py, db.py, errors.py, models.py        # kept, adapted
  realtime/   gateway.py (socket.io events), rooms.py (chunk rooms), ratelimit.py
  repo/       strokes.py, fates.py, canvases.py, users.py, billing.py, entities.py, jobs.py
  alive/      select.py, rasterize.py, vlm.py, postprocess.py, rig.py, bake.py, queue.py
  sim/        process.py (entry), bridge.py (main ↔ sim messaging), world.py, ecs.py,
              systems/ (needs, perception, decide, steer, interact, life), types/ (one module per type)
  auth/ billing/ ads/ i18n/ (server catalogs ru.json, en.json for emails + error fallbacks)
  schema.sql                                              # rewritten (§5), with triggers
web/src/
  camera.js, chunks.js (tile cache + subscribe), draw.js (kept), socket.js (adapted),
  render/ (strokes, entities, goo, vfx), ui/ (tools, alive, chooser, control, share, plans, gate),
  i18n/ (ru.json, en.json, i18n.js), analytics.js (kept)
config/  archetypes.yaml labels.yaml balance.yaml limits.yaml vlm.yaml billing.yaml flags.yaml
prompts/ classify.md
docs/agent/ step0-notes.md, deploy.md
```

### Main ↔ simulation process
- **Main → sim:**
  - `chunk_active` / `chunk_sleep {canvas, cx, cy}`
  - `spawn {entity}`
  - `input {entity_id, ax, ay}`
  - `interact {entity_id, action, to}`
  - `freeze {canvas}`
- **Sim → main:**
  - `delta {canvas, cx, cy, tick, upd, gone}` and `event {…}` → main broadcasts them to the chunk room
  - `snapshot {entities[]}` every 5 s → main UPSERTs
  - `explosion {canvas, x, y, r}` → main finds the static strokes within r, inserts `destroyed` fates and broadcasts `stroke:fate`
  - `death {entity_id, x, y}` → main inserts `fossil` fates
- **Transport:** two `multiprocessing` queues. A reader thread in main hands messages to the asyncio loop via `call_soon_threadsafe`.
- **If the sim process dies:** main restarts it and reloads living entities from the DB (≤ 5 s of world lost).

**Principles:**
- The server is the only source of truth for the world. The client renders and interpolates only.
- The VLM, storage, mail and payments each sit behind an interface with a **fake** implementation for tests.
- The world is split into **1024×1024 px chunks**. A client subscribes to **≤ 12 chunks** (the viewport plus a ring).

---

## 3. Object types (N = 13 + fallback)

Types are **presets of components** (ECS) defined in `config/archetypes.yaml`, not hard-coded classes. The VLM fills in the parameters. Later, paid custom objects will simply be an LLM writing a component config.

**Components:**
- `Locomotion{walk|fly|swim|hop|none, speed}`
- `Needs{hunger, thirst, energy}`
- `Diet{plants|meat|omni}`
- `Temper{passive|skittish|hostile}`
- `Health`, `Edible{nutrition}`, `Area{polygon}`
- `Spawner{what, period, cap}`, `Fuse{seconds, radius}`
- `Controllable{land|water|air}`, `Controller`
- `Role{villager|warrior|fisherman|medic}`
- `Flammable`, `Fire{spreads|campfire}`, `Shelter{capacity}`, `Reproduce`, `Weather{rain|sun}`

| # | id | RU / EN | People draw | Behaviour | End of life |
|---|---|---|---|---|---|
| 1 | `creature` | Существо / Creature | cow, dog, cat, bird, bee | wander. Hungry → seek food/plant. Thirsty → water. Flee predators, fire and lit fuses. Breed. Evening → nest/barn | hunger, thirst or damage → fossil |
| 2 | `predator` | Хищник / Predator | wolf, spider, monster, dragon | hunts creatures, people, and aquatics at the shore. Fights warriors. Eats meat | fossil |
| 3 | `aquatic` | Водное / Aquatic | fish, whale | only inside water; 10 HP/s damage outside. Eats food dropped in water. Breeds | caught → food "fish" |
| 4 | `person` | Человек / Person (+role) | villager, warrior, fisherman, medic | **Villager:** needs, sleeps in a house at night, hides from rain. **Warrior:** attacks predators within 200 px. **Fisherman:** goes to the shore; every 15 s catches an aquatic nearby, or every 60 s a "wild fish" → food. **Medic:** heals +5 HP/s within 30 px | fossil |
| 5 | `food` | Еда / Food | apple, meat, bread | eaten in 3 bites (sprite shrinks). Near fire it gets cooked (+50% nutrition) | disappears |
| 6 | `plant` | Растение / Plant | apple tree, bush, flower, pasture | **sways in the wind**. **Fruit tree:** drops a fruit every 60–90 s, ≤ 3 around it. **Pasture:** stock 0–100, a bite takes −20, regrows +1/s. Grows ×1.5–2 near water or in rain. Flammable | burns → fossil |
| 7 | `water` | Вода / Water (area) | lake, river, puddle | drink here. Fish live here. Fishing spot. Walkers slow to ×0.5. Cars can't enter; boats move only here. Puts out fire within 40 px. Puddles dry in sun and grow in rain | puddle dried → fossil |
| 8 | `house` | Дом / House | house, nest, barn | people rest and shelter from rain here. Breeding spot. Nest/barn for animals. Flammable | fossil |
| 9 | `explosive` | Взрывчатка / Explosive | grenade, bomb, TNT | **20 s fuse**, countdown visible to all; can be dragged. Fire nearby → explodes instantly. Radius = clamp(1.5×size, 80, 200) px; damage 100 at the centre, 30 at the edge. **Destroys entities and static strokes in the radius.** Ignites plants within radius/2. Chain reactions | disappears |
| 10 | `vehicle` | Транспорт / Vehicle | car, boat, plane | idle engine shake. Moves only under control, at 120 px/s. Hits walkers at >60 px/s (20 dmg + knockback) | fossil |
| 11 | `remote` | Пульт / Remote | remote, joystick, steering wheel | binds to the nearest unbound vehicle within 400 px (or the next one spawned). Click → **60 s control lease**, extended by input. One driver at a time | blast → unbinds |
| 12 | `fire` | Огонь / Fire | campfire, torch, wildfire | every 3 s, 30% chance to ignite a flammable thing within 40 px. Burns 20 s. 10 HP/s to inhabitants within 20 px. Water or rain puts it out. A campfire doesn't spread; people warm up next to it. **Never burns static strokes** | disappears |
| 13 | `sky` | Облако/Солнце / Cloud/Sun | cloud, sun | **Cloud:** drifts; rains 30 s every 2 min (fills water, grows plants, puts out fire, sends people indoors). **Sun:** plants within 400 px grow ×1.5 and puddles dry. Global day/night cycle of 20 min | doesn't die |
| — | `doodle` | Живая каракуля / Living scribble | heart, star, smiley, letters, abstract | hops and wobbles; can be pushed and dragged; edible only by omnivores | blast → fossil |

**Fossil:** on death, the entity's original strokes get a `fossil` fate. They render grey, shifted by `(death_x − origin_x, death_y − origin_y)`. Every stroke stays forever.

### Interaction matrix (row acts on column)
● = specific (behaviour above), ○ = baseline (collision, avoidance, push, plus blast/fire damage through `Health`/`Flammable`), · = none. Implement every cell and write a test per ● cell.

| who \ on whom | Сущ | Хищ | Водн | Чел | Еда | Раст | Вода | Дом | Взр | Трансп | Пульт | Огонь | Небо | Кар |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Creature | ● | ● | ○ | ○ | ● | ● | ● | ● | ● | ○ | · | ● | ○ | ○ |
| Predator | ● | ● | ● | ● | ● | · | ● | ○ | ● | ○ | · | ● | ○ | ○ |
| Aquatic | ○ | ● | ● | ○ | ● | · | ● | · | ○ | ○ | · | · | · | · |
| Person | ● | ● | ● | ● | ● | ● | ● | ● | ● | ○ | · | ● | ● | ○ |
| Food | · | · | · | · | · | · | ○ | · | · | · | · | · | · | · |
| Plant | · | · | · | · | ● | · | · | · | · | · | · | · | · | · |
| Water | ○ | ○ | ● | ○ | · | ● | · | · | · | ● | · | ● | · | · |
| House | ● | · | · | ● | · | · | · | · | · | · | · | · | · | · |
| Explosive | ● | ● | ● | ● | ● | ● | · | ● | ● | ● | ● | ● | · | ● |
| Vehicle | ○ | ○ | ○ | ○ | ○ | ○ | ● | ○ | ○ | ○ | ○ | ○ | · | ○ |
| Remote | · | · | · | · | · | · | · | · | · | ● | · | · | · | · |
| Fire | ● | ● | · | ● | ● | ● | · | ● | ● | ○ | · | · | · | ○ |
| Cloud/Sun | ○ | · | · | ● | · | ● | ● | · | · | · | · | ● | · | · |
| Scribble | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ○ | · | ○ |

### Balance defaults (`config/balance.yaml`)

| Parameter | Value |
|---|---|
| hunger 0→100 | 10 min |
| thirst 0→100 | 7 min |
| energy 100→0 | 15 min |
| damage at hunger/thirst = 100 | 2 HP/s |
| seek food when hunger > | 50 |
| speed S/M/L | 60 / 45 / 30 px/s |
| fly / hunt multiplier | ×1.3 / ×1.2 |
| apple / meat nutrition | +30 / +50 |
| flee radius | 150 px |
| **reproduction** | same type + same label; both hunger < 40, thirst < 40, energy > 50; distance < 60 px; cooldown 3 min. Baby = same sprite at scale 0.6, grows to 1.0 in 5 min. People only near a house; nest/barn gives ×2 chance for animals |
| population caps | ≤ 8 of one label per chunk, ≤ 60 living per chunk, ≤ 20 000 per world |

---

## 4. The ALIVE pipeline (≤ 8 s p95, hard limit 25 s)

1. **Request:** `POST /api/canvases/{id}/alive {lasso}`. Validate: role = editor; quota; 3–500 points; bbox ≤ 1024×1024; not frozen; ≤ 1 ALIVE per 5 s.
2. **Select strokes:** take the chunks overlapped by the bbox and keep strokes with **no fate** (static). A stroke is included if **≥ 70% of its points** are inside the polygon (even-odd rule). Limit 1–400 strokes. Reject a stroke that is alive or already under another job (in-memory lock keyed by `(canvas, seq)`).
3. Return **202 `{jobId, quota}`** and broadcast `alive:goo {jobId, polygon, by}` to the polygon's chunk rooms. Goo must appear ≤ 300 ms for the author and ≤ 500 ms for others.
4. **Rasterize** with skia-python using the Python port of `renderStroke`. Transparent background, alpha-trimmed with an 8 px pad, long side ≤ 512 px. Make a white-background copy for the VLM. **Do this off the event loop** (`asyncio.to_thread`).
5. **Classify** through the VLM adapter (async httpx): temperature 0, max_tokens 700, 10 s timeout, 1 retry, then the fallback model. Strict JSON schema (below). Model IDs live in `config/vlm.yaml`: primary Gemini-Flash-class, fallback Qwen-VL via an OpenRouter-compatible API. **Check current model IDs at implementation time.**
6. **Post-process:**
   - `config/labels.yaml` overrides the type by label, in either language (e.g. волк/wolf → predator; костёр/campfire → fire with campfire = true).
   - Clamp part boxes to 0–1 and drop any under 2% of the area.
   - Size class S/M/L from the bbox (< 96 / < 256 / ≥ 256 px).
   - `multiple=true` → `SELECTION_MULTIPLE`.
   - `unsafe=true` → `UNSAFE_CONTENT`; the goo fizzles.
   - If `confidence < 0.6` → `alive:choose {top3, deadline}` to the author; option #1 is chosen automatically after 20 s.
   - If the VLM times out, is unavailable or is over budget → send the manual type grid. **Never a dead end.**
7. **Rig and bake** (§6), off the event loop.
8. In one transaction: save the sprite, insert `stroke_fate(consumed, entity_id)` rows, and create the entity. Send `spawn` to the sim and broadcast `world:event {type:"spawn", jobId, entity}`; the goo pops.

The job queue is an in-process `asyncio` queue with concurrency 4 and ≤ 100 queued; job state is persisted in `alive_job`. **Quotas:** spent only on success or `UNSAFE_CONTENT`; refunded on our own failures. Per day, Moscow time: anonymous 10, free account 20, Plus 50.

### VLM output schema
```json
{
  "type": "creature|predator|aquatic|person|food|plant|water|house|explosive|vehicle|remote|fire|sky|doodle",
  "confidence": 0.0,
  "top3": [{"type": "creature", "p": 0.86}],
  "label": {"ru": "корова", "en": "cow"},
  "locomotion": "walk|fly|swim|hop|none",
  "diet": "plants|meat|omni|null",
  "role": "villager|warrior|fisherman|medic|null",
  "variant": "fruit_tree|pasture|campfire|cloud|sun|boat|car|plane|null",
  "facing": "left|right",
  "multiple": false,
  "unsafe": false,
  "parts": { "legs": [[x,y,w,h]], "wings": [], "wheels": [], "head": [x,y,w,h] | null, "tail": [x,y,w,h] | null }
}
```
`label` is given **in both languages**, nominative, lowercase. Part boxes are normalized 0–1. Validate the output with a Pydantic model. The prompt (`prompts/classify.md`, written in English) has a version header, and the version is stored in `alive_job.vlm_result`. Prompt rules:
- Pick the type by **function in the world**, not by drawing style.
- Anything unrecognizable → `doodle`.
- `unsafe` covers nudity, hate symbols and realistic violence against people.
- Drawings may contain handwriting in Russian or English; read it as a hint.

---

## 5. Data model (SQLite, fresh schema)

The schema is rewritten from scratch; the old DB file is discarded. Conventions:
- ids are TEXT (uuid4, or nanoid for canvas slugs)
- times are INTEGER unix ms
- JSON is stored as TEXT
- every enum gets a CHECK constraint
- `PRAGMA foreign_keys=ON`

```sql
CREATE TABLE app_user (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, display_name TEXT,
  locale TEXT CHECK (locale IN ('ru','en')), created_at INTEGER NOT NULL);
CREATE TABLE session (id TEXT PRIMARY KEY, user_id TEXT REFERENCES app_user(id), nickname TEXT,
  locale TEXT, created_at INTEGER NOT NULL, last_seen_at INTEGER);
CREATE TABLE subscription (id TEXT PRIMARY KEY, user_id TEXT UNIQUE NOT NULL REFERENCES app_user(id),
  plan TEXT CHECK (plan IN ('free','plus')), status TEXT CHECK (status IN ('active','past_due','canceled')),
  current_period_end INTEGER, yk_payment_method_id TEXT);
CREATE TABLE payment (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES app_user(id),
  yk_payment_id TEXT UNIQUE NOT NULL, amount_kop INTEGER NOT NULL, currency TEXT NOT NULL,
  status TEXT CHECK (status IN ('pending','succeeded','canceled')), created_at INTEGER NOT NULL);
CREATE TABLE canvas (id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('world','private')),
  owner_user_id TEXT REFERENCES app_user(id), title TEXT,
  link_role TEXT NOT NULL CHECK (link_role IN ('none','viewer','player','editor')),
  frozen INTEGER NOT NULL DEFAULT 0, bounds_w INTEGER NOT NULL, bounds_h INTEGER NOT NULL,
  stroke_count INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
  CHECK (kind = 'world' OR owner_user_id IS NOT NULL));
CREATE TABLE canvas_member (canvas_id TEXT REFERENCES canvas(id), user_id TEXT REFERENCES app_user(id),
  role TEXT CHECK (role IN ('viewer','player','editor')), invited_by TEXT, created_at INTEGER,
  PRIMARY KEY (canvas_id, user_id));
CREATE TABLE quota (subject TEXT, day TEXT, alive_used INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (subject, day));

-- Strokes: append-only and IMMUTABLE (kept legacy triggers). Points in world coords.
CREATE TABLE stroke (id INTEGER PRIMARY KEY AUTOINCREMENT, canvas_id TEXT NOT NULL REFERENCES canvas(id),
  seq INTEGER NOT NULL, client_id TEXT NOT NULL, session_id TEXT, color TEXT NOT NULL, width REAL NOT NULL,
  points TEXT NOT NULL, chunk_x INTEGER NOT NULL, chunk_y INTEGER NOT NULL, created_at INTEGER NOT NULL,
  UNIQUE (canvas_id, seq), UNIQUE (canvas_id, client_id));
CREATE INDEX stroke_chunk ON stroke (canvas_id, chunk_x, chunk_y, seq);
-- A stroke's fate: append-only and IMMUTABLE. Forward only: (none) → consumed → fossil, or (none) → destroyed.
CREATE TABLE stroke_fate (canvas_id TEXT NOT NULL, seq INTEGER NOT NULL,
  fate TEXT NOT NULL CHECK (fate IN ('consumed','destroyed','fossil')), entity_id TEXT, at INTEGER NOT NULL,
  PRIMARY KEY (canvas_id, seq, fate));
-- Current state = latest fate for (canvas_id, seq); no row = static.
-- Triggers: BEFORE UPDATE / BEFORE DELETE ON stroke and ON stroke_fate → RAISE(ABORT, ...).

CREATE TABLE alive_job (id TEXT PRIMARY KEY, canvas_id TEXT, session_id TEXT, lasso TEXT, stroke_seqs TEXT,
  status TEXT CHECK (status IN ('queued','raster','classify','choose','rig','spawned','failed')),
  vlm_result TEXT, chosen_type TEXT, error_code TEXT, timings TEXT, created_at INTEGER);
CREATE TABLE sprite (id TEXT PRIMARY KEY, job_id TEXT UNIQUE, cutout_path TEXT, sheet_path TEXT, manifest TEXT);
CREATE TABLE entity (id TEXT PRIMARY KEY, canvas_id TEXT, sprite_id TEXT, archetype TEXT, label TEXT /*{ru,en}*/,
  x REAL, y REAL, vx REAL, vy REAL, facing TEXT, state TEXT, hp REAL, hunger REAL, thirst REAL, energy REAL,
  params TEXT, origin_x REAL, origin_y REAL, death_x REAL, death_y REAL,
  parent_id TEXT, bound_to TEXT, summoner_session_id TEXT, born_at INTEGER, died_at INTEGER);
CREATE INDEX entity_alive ON entity (canvas_id) WHERE died_at IS NULL;
CREATE TABLE world_event (id INTEGER PRIMARY KEY AUTOINCREMENT, canvas_id TEXT, tick INTEGER, at INTEGER,
  type TEXT, actors TEXT, payload TEXT);
CREATE TABLE magic_token (token_hash TEXT PRIMARY KEY, email TEXT, locale TEXT, expires_at INTEGER, used_at INTEGER);
CREATE TABLE metric (key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0);
```

**Stroke insert:** the legacy transaction, kept. `BEGIN IMMEDIATE` → dedupe on `(canvas_id, client_id)` (a resubmit returns the existing `seq`) → `seq = stroke_count + 1` → insert with `chunk_x/chunk_y` (from the stroke's first point) → increment → `COMMIT`. **No `await` inside.** Broadcast only after commit.

**Rules:**
- Effective role = owner ? `editor` : `max(link_role, member.role)`. If the canvas is frozen, every role is downgraded to `viewer`.
- The shared world (`kind=world`, created at startup if missing) gives `editor` to everyone.
- The ЮKassa webhook is idempotent via `payment.yk_payment_id UNIQUE`.

**Retention** (daily job):
- `alive_job` and raw VLM output: 30 days
- cutout and sprite sheet files: 7 days after death (the fossil strokes stay)
- `world_event`: 90 days
- magic tokens: deleted on use or expiry
- payments: kept forever

Sprite files live in the data volume (`DATA_DIR/sprites/`) and are served by FastAPI with long cache headers.

**Backups:** keep the legacy SQLite online `.backup` approach (`deploy/backup.py` and its timer); include the sprites folder.

---

## 6. Rig and bake (procedural sprites from the user's lines)

- **Layers:** cut each part box into its own layer. The body keeps a 2 px overlap. No parts → a single layer.
- **Pivots:**
  - leg: middle of the top edge
  - wing: the edge nearest the body centre
  - wheel: centre
  - head: middle of the bottom edge
  - tail: the side touching the body

| anim | frames | how |
|---|---|---|
| idle | 4 | Breathing: scaleY 1→0.97→1. **Plants:** wind sway, an X-shear by horizontal strips that grows with height (±4% of width). **Vehicles:** ±1 px shake |
| move | 8 | **Legs:** ±20° in counter-phase pairs, plus a 3% body bob. **Wings:** scaleY 1→0.4→1. **Wheels:** +45°/frame. **Swim:** sine wave across vertical strips. **No parts:** squash-and-stretch hop |
| act | 4 | **Eat:** tilt forward 10°. **Attack:** 8% lunge. **Explosive:** pulse plus a red tint. **Fire:** flame wobble |
| death | 6 | flip 180°, desaturate, fade to fossil grey |

- **Bake:** each frame is the bbox plus 16% padding, ≤ 384 px. The frames go into `sheet.png`, ≤ 2048×2048 (Pillow/skia transforms). The client flips the sprite horizontally based on movement direction.
- **Not rigged:** `water` (original drawing + client ripple; polygon from the alpha mask via marching squares on a numpy array, ≤ 64 vertices) and `sky` (original drawing, drifting).
- **The manifest is the contract.** Future AI sprites must output the same format:
```json
{ "frameW": 212, "frameH": 168, "pivot": [106, 160], "facing": "right",
  "anims": { "idle": {"row":0,"frames":4,"fps":4}, "move": {"row":1,"frames":8,"fps":10},
             "act":  {"row":2,"frames":4,"fps":8}, "death": {"row":3,"frames":6,"fps":8} } }
```

---

## 7. World simulation (separate process, 10 Hz)

**Tick order:**
1. inputs
2. needs
3. perception (spatial hash, 128 px cells)
4. decision (utility-scored state machine)
5. steering (wander/seek/flee/arrive) + avoidance
6. interactions from the matrix
7. births, deaths and fruit spawns
8. deltas and events
9. mark dirty

**Performance in Python:**
- Keep positions and velocities in numpy arrays and move them vectorized.
- **Stagger decisions:** each entity re-decides at ~2 Hz (one fifth of entities per tick), and immediately on threat.
- Only active chunks tick.
- **Targets:** p95 tick ≤ 60 ms with **2 000 active entities**; client 60 fps with 200 visible; control latency ≤ 150 ms.
- If the tick target isn't met, drop to **5 Hz** (the client already interpolates) and tell the human.

**States:** `wander, seek_food, eat, seek_water, drink, rest, mate, flee, hunt, attack, dead`. `flee` pre-empts everything within 150 px.

**Chunks:**
- **Active** = a client is subscribed to it or to a neighbour, or it contains a lit fuse or fire.
- Sleeping chunks freeze.
- On wake, run an analytic catch-up for `Δt = min(elapsed, 10 min)`; positions don't change.

**Persistence:** the sim sends snapshots of dirty entities every 5 s and on sleep, and batched events every 1 s; **main writes them**.

**Network** (broadcast by main):
- `world:snapshot` on subscribe.
- `world:delta {tick, upd:[[id,x,y,st,anim,hp%]…], gone:[…]}` 10×/s per chunk room, changed fields only.
- `world:event` for VFX and the feed.

**Client world:**
- Pan (hand tool or space-drag, two fingers) and zoom (wheel, pinch), clamped to the world bounds.
- Strokes are cached as one bitmap per chunk; entities are drawn on a separate layer.
- Deep links: `/w#x=…&y=…&z=…`.
- Newcomers spawn near activity.

---

## 8. Contracts

### REST

| Method & path | Who | Request → Response |
|---|---|---|
| GET `/api/me` | anyone | → `{user?, plan, ads, locale, quota:{used,limit}}` |
| PUT `/api/me/locale` | anyone | `{locale:"ru"\|"en"}` → 204 |
| POST `/api/auth/magic-link` | anyone | `{email}` → 204 (≤ 3 mails / 10 min / address; mail in the session locale) |
| GET `/api/auth/callback?token=` | anyone | → 302 + Set-Cookie (httpOnly, SameSite=Lax); links the anonymous session to the user |
| POST `/api/auth/logout` | logged in | → 204 |
| POST `/api/canvases` | Plus | `{title}` → 201 `{id, kind:"private"}` (≤ 5 worlds) |
| GET `/api/canvases/{id}` | role ≥ viewer | → `{id, kind, title, role, frozen, bounds, spawn:{x,y}, limits, palette}` |
| GET `/api/canvases/{id}/chunks/{cx}/{cy}?afterSeq=` | role ≥ viewer | → `{strokes[], fates[], entities[], lastSeq, tick}` |
| POST `/api/canvases/{id}/alive` | editor | `{lasso:[[x,y],…]}` → 202 `{jobId, quota}` |
| GET `/api/alive/{jobId}` | author | → `{status, type?, entityId?, error?}` |
| POST `/api/alive/{jobId}/choice` | author | `{type}` → 200 |
| GET / PUT `/api/canvases/{id}/access` | owner | `{linkRole}` |
| POST `/api/canvases/{id}/members` | owner | `{email, role}` → 201 (invite mail) |
| PATCH / DELETE `/api/canvases/{id}/members/{userId}` | owner | `{role}` / — |
| GET `/api/me/events?since=` | anyone | events involving my entities |
| POST `/api/billing/checkout` | logged in | `{plan:"plus_month"\|"plus_year"}` → `{confirmationUrl}` |
| POST `/api/billing/webhook` | ЮKassa | **re-fetch the payment via the API before trusting it** + IP allowlist |
| POST `/api/billing/cancel` | logged in | → 200 (active until period end) |
| POST `/api/metrics/{key}` | anyone | → 204 (whitelisted keys) |
| GET `/api/health` | anyone | → `{ok:true}` |

### Socket.io

| Dir | Event | Payload | Rate |
|---|---|---|---|
| c→s | `viewport:set` | `{canvasId, chunks:[[cx,cy]…]}` ≤ 12 | ≤ 4/s |
| c→s | `stroke:add` | `{canvasId, clientId, color, width, points}` (ack `{ok, seq}`) | ≤ 30 / 10 s, editor |
| c→s | `entity:interact` | `{entityId, action:"pet"\|"push"\|"drag", to?}` | ≤ 5/s, role ≥ player, cursor ≤ 64 px from the entity |
| c→s | `control:claim` / `control:release` | `{controllerId}` | click |
| c→s | `control:input` | `{ax, ay}` ∈ [−1, 1] | 20/s while leased |
| s→c | `stroke:new`, `stroke:fate` | stroke / `{seqs[], fate, entityId?}` | chunk room |
| s→c | `alive:goo` / `alive:progress` / `alive:choose` / `alive:failed` | §4 | |
| s→c | `world:snapshot` / `world:delta` / `world:event` | §7 | |
| s→c | `control:granted` / `control:revoked` | `{vehicleId, until}` / `{reason}` | |
| s→c | `canvas:frozen` | `{frozen}` | |

**Roles:**
- **viewer:** watches.
- **player:** can pet, push, drag and drive; no drawing and no ALIVE.
- **editor:** everything.

Draggable types are `food`, `explosive`, `remote` and `doodle`. Check roles **server-side** on every call and event.

### Error envelope (legacy shape, extended)
`{"ok": false, "error": {"code", "message", "field", "retryable", "details"}}`
- `message` is localized to the request locale (the session/user locale, else `Accept-Language`).
- **The client renders its own localized text from `code` + `details`.** `message` is only a fallback.

Codes:
- VALIDATION_FAILED 400, UNAUTHENTICATED 401, FORBIDDEN_ROLE 403, PAYMENT_REQUIRED 402
- CANVAS_FROZEN 423, NOT_FOUND 404, QUOTA_EXCEEDED 429, RATE_LIMITED 429
- SELECTION_EMPTY 422, SELECTION_TOO_LARGE 422, SELECTION_MULTIPLE 422, SELECTION_BUSY 409, STROKES_ALREADY_ALIVE 409
- ENTITY_LIMIT 409, UNSAFE_CONTENT 422, VLM_TIMEOUT / VLM_UNAVAILABLE (→ manual type grid, not shown as an error)
- CHOICE_EXPIRED 410, CONTROL_TAKEN 409, PAYMENT_FAILED 402, PAYLOAD_TOO_LARGE 413, INTERNAL 500

Socket reconnect uses the kept backoff (1, 2, 4 s …) and resyncs each subscribed chunk from its contiguous `seq` prefix.

---

## 9. Ads, adblock gate, plans, auth, billing

- **Show ads** when the viewer's plan is free (including anonymous) **and** `ads.enabled`. Ads depend on the **viewer's** plan, never the host's.
- **Layout:** desktop gets vertical РСЯ blocks left and right of the canvas (160×600 / 300×600). Narrow screens get one adaptive block at the bottom.
- **Adblock detector (client):**
  1. Load the РСЯ loader with `onerror`.
  2. Add a bait `div.adsbox.ad-banner` (1×1).
  3. After 1.5 s, blocked = the script failed **or** the bait is hidden. **An empty ad fill is NOT a block.**
  4. If blocked: don't mount the canvas, don't connect the socket, show the gate.
  5. Metrics: `adblock_gate_shown`, `adblock_gate_passed`, `plus_clicked`.
- **Private worlds:** only the host needs Plus; guests on any plan can join.
- **Freeze:** if the owner's subscription is `past_due` for more than 3 days, or `canceled` after the period end → `frozen=1`. The sim pauses and everyone becomes a viewer. Paying unfreezes immediately. Never delete.
- **Billing (ЮKassa test mode, via httpx):**
  1. `checkout` creates a payment of 299.00 RUB, or 2 490.00 RUB for a year, with `save_payment_method`, an Idempotence-Key and `metadata.userId`.
  2. The user is redirected to `confirmation_url`.
  3. The `payment.succeeded` webhook arrives → `GET /v3/payments/{id}` → payment succeeded, subscription active, `period_end` extended.
  4. A daily 03:00 MSK renewal task (asyncio background task) runs when `billing.recurring` is on. Failure → `past_due`.
  5. Prices are **always in rubles**, in either UI language. Receipts come from «Чеки от ЮKassa».
- **Auth:**
  - Magic link: 32-byte token (`secrets.token_urlsafe`) stored as SHA-256, 15 min TTL, single use.
  - On login, link the anonymous session to the user; its strokes, entities and quota stay with it.
  - Store only e-mail + locale; show a privacy notice and consent at login, in both languages.
  - The legacy "no personal data" rule is retired; e-mail is now the only personal data stored.

## 9a. Languages: Russian + English

- **Default:** `ru` if `navigator.language` starts with `ru`, otherwise `en`. A **RU / EN switch** sits in the top bar and the profile. It's saved in localStorage (wrapped in try/catch, as in legacy), in the session, and in `app_user.locale`.
- **Client catalogs:** `web/src/i18n/ru.json` and `en.json` with **identical keys**. Plurals use the built-in `Intl.PluralRules` (Russian has one/few/many: «1 оживление, 2 оживления, 5 оживлений»).
- **A test fails** if a key is missing in either catalog, or if a Cyrillic or English user-facing string literal appears outside the catalogs.
- **Server catalogs** (`app/i18n/`) hold emails and error `message` fallbacks, with the same rule.
- **Object labels:** from the VLM's `label.{ru,en}`. Name tags avoid declension: «корова · Саша» / "cow · Sasha".
- **Never** concatenate translated fragments. Use whole sentences with placeholders.
- `<html lang>` and the document title follow the locale.

| Key | RU | EN |
|---|---|---|
| alive.button | ОЖИВИ | ALIVE |
| alive.hint | Обведи рисунок, который хочешь оживить. | Draw a loop around the drawing you want to bring to life. |
| alive.left | Осталось ✨ сегодня: {n} из {limit} | ✨ left today: {n} of {limit} |
| goo.title | Оживаем… | Coming alive… |
| goo.stage.classify | смотрим, что это | figuring out what it is |
| goo.stage.rig | оживляем | bringing it to life |
| choose.title | Это скорее… | Is it more of a… |
| choose.other | Другое… | Something else… |
| control.driving | Ты рулишь: {label} · {s} с | You're driving: {label} · {s} s |
| control.release | Отпустить | Let go |
| control.taken | Сейчас рулит {name} — ещё {s} с | {name} is driving — {s} s left |
| control.unbound | Пульт не привязан — нарисуй машину рядом | This remote isn't linked — draw a car nearby |
| canvas.loading | Загружаем мир… | Loading the world… |
| canvas.offline | Связь потеряна, переподключаемся… | Connection lost, reconnecting… |
| canvas.empty | Нарисуй что-нибудь и нажми ✨ | Draw something and press ✨ |
| canvas.frozen | Мир на паузе: у хозяина закончилась подписка | This world is paused: the host's subscription has ended |
| roles.viewer / player / editor | Наблюдатель / Игрок / Редактор | Viewer / Player / Editor |
| share.empty | Пока здесь только ты — пригласи друзей | It's just you here — invite friends |
| plan.plus | «Плюс» | Plus |
| plan.active | Готово! «Плюс» активен до {date} | Done! Plus is active until {date} |
| gate.title | Привет! Прости, но всё это стоит денег | Hey! Sorry, but all this costs money |
| gate.body | Оживление рисунков работает на нейросетях и серверах, и реклама помогает держать бесплатный мир открытым. Отключи блокировщик для collabpad.ru — или попробуй «Плюс»: | Bringing drawings to life runs on neural networks and servers, and ads keep the free world open. Turn off your ad blocker for collabpad.ru — or try Plus: |
| gate.perks | ✓ без рекламы · ✓ приватные миры и доступ по ролям · ✓ больше ✨ оживлений | ✓ no ads · ✓ private worlds with role-based access · ✓ more ✨ revivals |
| gate.retry / gate.plus | Я отключил — проверить / Попробовать «Плюс» | I turned it off — check again / Try Plus |
| err.FORBIDDEN_ROLE | Оживлять могут только редакторы этого мира | Only editors of this world can bring drawings to life |
| err.NOT_FOUND | Такого мира нет | This world doesn't exist |
| err.QUOTA_EXCEEDED | ✨ на сегодня закончились. {hint} | You're out of ✨ for today. {hint} |
| err.QUOTA_EXCEEDED.hint.anon / free | Войди — и получишь ещё 10 / «Плюс»: 50 в день | Sign in to get 10 more / Plus: 50 a day |
| err.RATE_LIMITED | Подожди пару секунд | Wait a couple of seconds |
| err.SELECTION_EMPTY | Обведи рисунок | Draw a loop around a drawing |
| err.SELECTION_TOO_LARGE | Слишком большой рисунок — обведи что-то одно | That's too big — loop just one thing |
| err.SELECTION_MULTIPLE | Тут несколько рисунков — обведи один | There are several drawings here — loop just one |
| err.SELECTION_BUSY | Этот рисунок уже оживляют | Someone is already bringing this to life |
| err.ENTITY_LIMIT | Здесь слишком людно — попробуй рядом | It's too crowded here — try nearby |
| err.UNSAFE_CONTENT | Это оживить нельзя | This can't be brought to life |
| err.PAYMENT_FAILED | Оплата не прошла | Payment failed |
| err.INTERNAL | Что-то сломалось, мы уже чиним | Something broke — we're on it |

**Type titles** come from §3 (RU / EN columns).

---

## 10. UI states

| Screen | Loading | Success | Error | Empty |
|---|---|---|---|---|
| World | skeleton + `canvas.loading` | world lives; feed bottom-right | `canvas.offline` (read-only) | `canvas.empty` hint |
| ✨ / lasso | — | dashed lasso + **ОЖИВИ / ALIVE** + `alive.left` | error text under the lasso | `alive.hint` |
| Goo | animated goo + stages | goo pops, object jumps out with a name tag | goo fizzles, strokes stay, toast | — |
| Chooser | — | top-3 chips + `choose.other` (grid of 13) | auto-pick #1 after 20 s | — |
| Control | — | `control.driving`, WASD or virtual joystick, `control.release` | `control.taken` / `control.unbound` | — |
| Share | spinner | member added | invite failed | `share.empty` |
| Plans / pay | redirecting… | `plan.active` (ads vanish) | `err.PAYMENT_FAILED` | — |

**Routes:**
- `/` — landing, new copy in RU/EN in the legacy visual style
- `/w` — shared world
- `/p/{slug}` — private world
- `/plans`
- `/billing/return`
- old `/c/{id}` redirects to `/w`

---

## 11. Plan of work (≈ 261 h)

Track **A** = world + ALIVE; track **B** = accounts, money, ads, i18n. If you're one agent, do A's critical path first and interleave B.

| ID | Task | Track | h | Depends | Acceptance |
|---|---|---|---|---|---|
| T0 | Step 0: read-through, notes, delete unneeded code, restructure; kept modules + tests green | A | 6 | — | `pytest -q` and `npm test` green on the skeleton |
| T1 | Config files, flags, env; adapt Dockerfile/Compose/`start.sh`/`LOCAL.md` (sprites in the data volume, sim process inside the app container) | A/B | 4 | T0 | `docker compose up` boots with all flags off |
| T2 | Fresh schema (§5) + immutability triggers on `stroke` and `stroke_fate`; shared world created at startup | A | 5 | T1 | UPDATE/DELETE raise; stroke insert is idempotent |
| T3 | Client camera (pan/zoom), chunk tile cache, drawing in world coords, ✨ lasso | A | 10 | T0 | 2 clients draw in a big world |
| T4 | Server stroke selection + locks + validation | A | 5 | T2 | tests for the 70% rule, limits, busy/alive |
| T5 | Python port of `renderStroke` (skia-python) + rasterize | A | 5 | T4 | golden image vs. browser render within tolerance |
| T6 | VLM adapter (httpx), prompt, Pydantic schema, retries, fallback, post-processing | A | 8 | T5 | fake adapter; bad JSON → retry → fallback |
| T7 | 60+ test drawings (RU and EN handwriting included) + accuracy script | A | 4 | T6 | ≥ 80% top-1 |
| T8 | Rig + bake + fallback anim (Pillow/skia, numpy) | A | 16 | T6 | sheets for legs/wings/wheels/none |
| T9 | Job queue, statuses, `alive:*` events | A | 5 | T4 | goo ≤ 300 ms; choose/timeout/fail |
| T10 | Sim process: spawn/restart, main↔sim bridge, tick, ECS, spatial hash, chunks, snapshots via main | A | 18 | T2 | 2 000 entities, p95 tick ≤ 60 ms (or 5 Hz fallback reported) |
| T11 | Shared components + utility FSM (staggered decisions) + steering | A | 10 | T10 | unit tests |
| T12 | 13 types + scribble (split below) | A+B | 58 | T11, T8 | one test per ● cell |
| T13 | Reproduction + caps | A | 5 | T12 | caps hold in a 30 min soak |
| T14 | Sleep catch-up | A | 4 | T12 | deterministic test |
| T15 | Snapshot/delta/event protocol, interpolation, entity layer | A | 12 | T10, T3 | 2 clients see the same world |
| T16 | ALIVE UI: button, goo, chooser, grid, errors | A | 8 | T3, T9 | every error code has RU + EN text |
| T17 | Control lease + WASD/joystick overlay | B | 5 | T12, T15 | lease, extend, release on disconnect |
| T18 | VFX: blast, eat, birth, fire, rain | B | 6 | T15 | — |
| T19 | Magic-link auth + session linking + `/api/me` | B | 8 | T2 | token single-use/expiry |
| T20 | Private worlds, roles, middleware, share dialog | B | 12 | T19 | role matrix tests (REST + socket) |
| T21 | ЮKassa test mode: checkout, webhook, renewal, freeze, `/plans` | B | 12 | T19 | duplicate webhook idempotent; freeze/unfreeze |
| T22 | РСЯ slots + adblock detector + gate | B | 5 | T1 | empty fill ≠ blocked; mobile works |
| T23 | Quotas + rate limits | B | 3 | T19 | daily reset (Moscow), refunds |
| T24 | **i18n**: catalogs, switch, locale persistence, plurals, emails, missing-key test, landing in RU/EN | B | 6 | T0 | every screen in both languages |
| T25 | Load test (2 000 entities, 50 clients) | A | 4 | T15, T12 | p95 tick time, bandwidth report |
| T26 | Scenario testing, fixes, balance | all | 12 | all | §12 passes |
| T27 | `docs/agent/deploy.md`: adapted Nginx/systemd/Compose, backup incl. sprites, **exact commands to wipe the prod DB and deploy**; **no deploying** | A | 5 | T26 | the human can follow it step by step |

**T12 split (h):**
- Creature 4, Predator 3, Aquatic 3
- Person 8 (villager 3, warrior 2, fisherman 2, medic 1)
- Food 2, Plant 4, Water 6, House 4
- Explosive 4, Vehicle 3, Remote 5
- Fire 5, Cloud/Sun 5, Scribble 2

**Critical path:** T0→T1→T2→T10→T11→T12→T13→T25→T26→T27 = 127 h.

**Cut order if behind** (tell the human before cutting): Cloud/Sun → Fire → House → fisherman/medic roles → sleep catch-up.

---

## 12. Definition of done (two browser clients; run once in RU and once in EN)

1. An anonymous user opens `/w` and lands near activity. They pan and zoom across a big world, draw a cow, a pond and an apple tree, and press **ОЖИВИ** (or **ALIVE** in EN) on each. Both clients see the goo and then the objects, with name tags in each client's own language.
2. The cow grazes or eats apples and drinks from the pond. With no food it dies and becomes a grey fossil where it died. The stroke rows are untouched; only `stroke_fate` rows were added.
3. A wolf hunts the cow; the cow flees; a drawn warrior attacks the wolf.
4. Two fed cows have a calf (a smaller copy of the same drawing). Caps hold.
5. A grenade counts down from 20 s, explodes, and destroys entities and static strokes in the radius (as `destroyed` fates). Fire next to a bomb detonates it instantly. Fire never burns static strokes.
6. A car does nothing until a remote is drawn nearby. A player drives it with WASD or the joystick; the other client sees `control.taken`.
7. A cloud rains: the puddle grows and a fire goes out.
8. An ambiguous drawing → chooser. With the VLM down (fake adapter) → manual grid, and it still comes alive.
9. The 11th ALIVE for an anonymous user that day → `QUOTA_EXCEEDED` with the sign-in hint, in the right language and with correct Russian plurals.
10. Magic-link login → buy Plus in ЮKassa test mode (rubles in both languages) → ads disappear → create a private world → share it with role Player to a free account. The guest can pet and drive but not draw or ALIVE, and still sees ads.
11. Expire the host's subscription (test clock) → the world freezes for everyone → pay → it unfreezes.
12. With `ads.enabled` and an adblocker on as a free user → the gate; turn it off → retry → the canvas opens. An empty ad fill doesn't trigger the gate.
13. Killing the sim process mid-game → it restarts and the world resumes (≤ 5 s lost).
14. Switching RU ↔ EN changes every visible string. The missing-key test passes. The load test meets §7; `docker compose up --build` works from a clean clone; all tests are green.

Report back with: what's done, what's cut, test results, open questions, and `docs/agent/deploy.md` for the human.
