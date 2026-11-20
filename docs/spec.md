# CollabPad «Живой холст» / "Living Canvas": product spec

**collabpad.ru** is a shared play world where drawings come alive. This file describes the
system **as it is built**. Code, config and tests cite it by section ("spec §5").

- **Keep it true.** A change to behaviour, a contract or the schema updates this file in the
  same commit. If the code and this spec disagree, one of them is a bug: find out which, ask
  the human if unsure, and fix it.
- Config files are authoritative for numbers (`config/*.yaml`), and `app/schema.sql` for the
  schema. The tables here summarize them.
- What is not done or not verified: [`status.md`](status.md). Rules for agents: the root
  `AGENTS.md`.
- Background (Russian, older): `docs/background/`. The TZ there predates this spec. Where they
  differ (stack, storage model, languages, button text), this spec wins.

---

## 1. What it is

The user draws (no undo), clicks **✨**, lassoes a drawing and presses **ОЖИВИ** (RU) / **ALIVE** (EN).
1. While the server works, the selected area is covered in animated **fairy goo** («волшебная слизь») that every viewer nearby sees.
2. A VLM sorts the drawing into one of **13 object types**, or the fallback "living scribble", and marks body parts.
3. The server builds a **procedural rig from the user's own lines** and bakes a sprite sheet.
4. The goo pops, and the object starts living in a **server-authoritative world simulation**: it eats, drinks, hunts, breeds, burns, explodes, or can be driven by remote control. All of this is synced to every viewer.

**Plans:**
- **Free:** one big shared world (bounded but huge), Yandex ad banners on both sides, canvas blocked if an adblocker is on.
- **«Плюс» / "Plus" (299 ₽/month):** no ads, up to 5 private worlds with link or account access and roles, more ALIVEs.
- **Only the host pays.** Guests are free and still see ads.

**No undo.** Every stroke stays forever. Stroke rows are never updated or deleted; what happens
to a stroke later (brought to life, destroyed, fossilized) is a separate append-only fate (§5).

---

## 2. Stack, layout and config

| Layer | Choice | Notes |
|---|---|---|
| Server | **Python 3.12, FastAPI, python-socketio**, Uvicorn with **exactly 1 worker** | rooms, control leases and the sim bridge live in one process's memory |
| Simulation | a **separate Python process** (`multiprocessing`, spawn) started in the FastAPI lifespan and restarted if it dies | keeps the event loop free; the GIL makes a thread unsuitable |
| DB | **SQLite** (WAL), stdlib `sqlite3`, plain SQL in `app/repo/` | **the main process is the only DB writer**; the sim sends it snapshots and events |
| Raster | **skia-python** | Python port of the client's `renderStroke` (same curves, caps and pressure width); golden test against a browser render |
| Image / maths | **Pillow**, **numpy** | rig, bake, alpha trim, water outline, vectorized sim movement |
| HTTP client | **httpx** | VLM, Robokassa recurring charges |
| Config | PyYAML + pydantic-settings | camelCase on the wire, snake_case in code |
| Client | **Vanilla JS** (ES modules) + Vite + Canvas 2D + socket.io-client | no framework, no TypeScript |
| Tests | pytest + pytest-asyncio; `node --test` for client modules; Playwright for the §12 scenarios (`npm run e2e`) | |
| Infra | Docker Compose (Node build stage + Python runtime), the host's Nginx in front | **Docker Compose only.** A push to the `deploy` branch deploys through GitHub Actions; the human owns production ([`deploy.md`](deploy.md)) |

New dependencies need the human's approval.

```
app/
  main.py config.py db.py errors.py models.py retention.py schema.sql
  realtime/   Socket.IO gateway, chunk rooms, control leases, rate limits
  repo/       SQL by table family (strokes, fates, canvases, users, billing, entities, jobs, events, metrics, retention)
  alive/      stroke selection, rasterize, VLM adapter, post-processing, parts, rig, bake, built-in sprites, job queue
  sim/        process entry, main ↔ sim bridge, world, ECS, systems/, types/ (one module per type)
  auth/ billing/ ads/ i18n/ (server catalogs: e-mails and error fallbacks)
web/src/
  camera, chunks (tile cache + subscribe), draw, socket, api, ads, analytics,
  render/ (strokes, entities, goo, vfx), ui/ (tools, alive, chooser, control, share, plans, gate), i18n/
config/  archetypes labels balance limits vlm billing flags (.yaml)
prompts/ classify.md
scripts/ loadtest.py, vlm_accuracy.py, drawings.py
deploy/  host Nginx configs, certbot renew hook, backup.py
```

**Flags** (`config/flags.yaml`; everything must boot with all of them off): `ads.enabled`
(false), `billing.recurring` (false), `vlm.enabled` (true), `sleep_catchup` (true).

**Secrets** come only from env vars (`.env`, documented in `.env.example`). Robokassa runs in
**test mode** only: every request carries `IsTest=1` and is signed with the shop's *test*
passwords (`ROBOKASSA_TEST_PASSWORD1/2`); Robokassa rejects production passwords in test mode. The VLM has a
**daily budget cap** (`config/vlm.yaml`).

### Main ↔ simulation process
- **Main → sim:**
  - `chunk_active` / `chunk_sleep {canvas, cx, cy}`
  - `spawn {entity}`
  - `input {entity_id, ax, ay}`
  - `interact {entity_id, action, to}`
  - `freeze {canvas}`
- **Sim → main:**
  - `delta {canvas, cx, cy, tick, upd, gone, enter}` and `event {…}` → main broadcasts them (§7)
  - `snapshot {entities[]}` every 5 s → main UPSERTs
  - `explosion {canvas, x, y, r}` → main finds the static strokes within r, inserts `destroyed` fates and broadcasts `stroke:fate`
  - `death {entity_id, x, y}` → main inserts `fossil` fates
- **Transport:** two `multiprocessing` queues. A reader thread in main hands messages to the asyncio loop via `call_soon_threadsafe`.
- **If the sim process dies:** main restarts it and reloads living entities from the DB (≤ 5 s of world lost).

**Principles:**
- The server is the only source of truth for the world. The client renders and interpolates only.
- The VLM, storage, mail and payments each sit behind an interface with a **fake** implementation for tests.
- The world is split into **1024×1024 px chunks**. The shared world is **19 456 × 11 264 px (19 × 11 chunks)**, set in `config/limits.yaml` and re-applied at each startup.
- **Every stroke lies in one chunk.** The client cuts strokes at chunk borders; the server rejects a stroke that spans two.
- A client subscribes to **≤ 12 chunks** (the viewport plus a ring); zoom-out is limited so the viewport never needs more.

---

## 3. Object types (N = 13 + fallback)

Types are **presets of components** (ECS) defined in `config/archetypes.yaml`, not hard-coded classes. The VLM fills in the parameters. Paid custom objects (§11) would be an LLM writing a component config.

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
| 4 | `person` | Человек / Person (+role) | villager, warrior, fisherman, medic | **Villager:** needs, sleeps in a house at night, hides from rain. **Warrior:** attacks predators within 200 px (20 damage per think, so he narrowly beats a lone wolf). **Fisherman:** goes to the shore; every 15 s catches an aquatic nearby, or every 60 s a "wild fish" → food. **Medic:** heals +5 HP/s within 30 px. Any hungry person shakes a fruit tree for fruit | fossil |
| 5 | `food` | Еда / Food | apple, meat, bread | eaten in 3 bites (sprite shrinks). Near fire it gets cooked (+50% nutrition) | disappears |
| 6 | `plant` | Растение / Plant | apple tree, bush, flower, pasture | **sways in the wind**. **Fruit tree:** drops a fruit every 60–90 s, ≤ 3 around it. **Pasture:** stock 0–100, a bite takes −20, regrows +1/s. Grows ×1.5–2 near water or in rain. Flammable | burns → fossil |
| 7 | `water` | Вода / Water (area) | lake, river, puddle | drink here. Fish live here. Fishing spot. Walkers slow to ×0.5. Cars can't enter; boats move only here. Puts out fire within 40 px. Puddles dry in sun and grow in rain | puddle dried → fossil |
| 8 | `house` | Дом / House | house, nest, barn | people rest and shelter from rain here. Breeding spot. Nest/barn for animals. Flammable | fossil |
| 9 | `explosive` | Взрывчатка / Explosive | grenade, bomb, TNT | **20 s fuse**, countdown visible to all; can be dragged. Fire nearby → explodes instantly. Radius = clamp(1.5×size, 80, 200) px; damage 100 at the centre, 30 at the edge. **Destroys entities and static strokes in the radius.** Ignites plants within radius/2. Chain reactions | disappears |
| 10 | `vehicle` | Транспорт / Vehicle | car, boat, plane | idle engine shake. Moves only under control, at 120 px/s. Hits walkers at >60 px/s (20 dmg + knockback) | fossil |
| 11 | `remote` | Пульт / Remote | remote, joystick, steering wheel | binds to the nearest unbound vehicle within 400 px (or the next one spawned). Click → **60 s control lease**, extended by input. One driver at a time | blast → unbinds |
| 12 | `fire` | Огонь / Fire | campfire, torch, wildfire | every 3 s, 30% chance to ignite a flammable thing within 40 px. Wild fire burns 20 s; **a campfire burns until doused** and doesn't spread; people warm up next to it. 10 HP/s to inhabitants within 20 px. Water or rain puts it out. Burning a plant or house is a child fire entity on it; the host becomes a fossil when that fire burns out. **Never burns static strokes** | disappears |
| 13 | `sky` | Облако/Солнце / Cloud/Sun | cloud, sun | **Cloud:** drifts; rains 30 s every 2 min (fills water, grows plants, puts out fire, sends people indoors). **Sun:** plants within 400 px grow ×1.5 and puddles dry. Global day/night cycle of 20 min | doesn't die |
| — | `doodle` | Живая каракуля / Living scribble | heart, star, smiley, letters, abstract | hops and wobbles; can be pushed and dragged; edible only by omnivores | blast → fossil |

Fruit, wild fish and flames are not drawn by anyone: they use built-in sprites (`app/alive/builtin.py`).

**Fossil:** on death, the entity's original strokes get a `fossil` fate. They render grey, shifted by `(death_x − origin_x, death_y − origin_y)`. Fossils are served and drawn by where the entity died (`fossils` in the chunk response, a `world:event` of type `fossil`), since that can be a different chunk from the strokes.

### Interaction matrix (row acts on column)
● = specific (behaviour above), ○ = baseline (collision, avoidance, push, plus blast/fire damage through `Health`/`Flammable`), · = none. Every cell is implemented, and every ● cell has a test (`tests/test_sim_types.py`).

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

### Balance (`config/balance.yaml`)

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
| population caps | ≤ 8 of one label per chunk, ≤ 60 living per chunk, ≤ 20 000 per world. Checked when something is born or spawned, so walking between chunks can put more than 8 of a label in one chunk |

---

## 4. The ALIVE pipeline (≤ 8 s p95, hard limit 25 s)

1. **Request:** `POST /api/canvases/{id}/alive {lasso}`. Validate: role = editor; quota; 3–500 points; bbox ≤ 1024×1024; not frozen; ≤ 1 ALIVE per 5 s.
2. **Select strokes:** take the chunks overlapped by the bbox and keep strokes with **no fate** (static). A stroke is included if **≥ 70% of its points** are inside the polygon (even-odd rule). Limit 1–400 strokes. Reject a stroke that is alive or already under another job (in-memory lock keyed by `(canvas, seq)`).
3. Return **202 `{jobId, quota}`** and broadcast `alive:goo {jobId, polygon, by}` to the polygon's chunk rooms. Goo must appear ≤ 300 ms for the author and ≤ 500 ms for others.
4. **Rasterize** with skia-python using the Python port of `renderStroke`. Transparent background, alpha-trimmed with an 8 px pad, long side ≤ 512 px. A white-background copy goes to the VLM. **Off the event loop** (`asyncio.to_thread`).
5. **Classify** through the VLM adapter (async httpx, OpenRouter-compatible API): temperature 0, max_tokens 700, 10 s timeout, 1 retry, then the fallback model; after a timeout it goes straight to the fallback. Strict JSON schema (below). Model IDs in `config/vlm.yaml`. The VLM gets what is left of the 25 s hard limit minus 5 s for rigging; past that the author gets the type grid. `VLM_PROXY_URL` sends the VLM calls, and only those, through an HTTP proxy; the app's HTTP clients ignore `HTTPS_PROXY`.
6. **Post-process:**
   - `config/labels.yaml` overrides the type by label, in either language (e.g. волк/wolf → predator; костёр/campfire → fire with campfire = true).
   - Clamp part boxes to 0–1 and drop any under 2% of the area.
   - Size class S/M/L from the bbox (< 96 / < 256 / ≥ 256 px).
   - `multiple=true` → `SELECTION_MULTIPLE`.
   - `unsafe=true` → `UNSAFE_CONTENT`; the goo fizzles.
   - If `confidence < 0.6` → `alive:choose {top3, deadline}` to the author; option #1 is chosen automatically after 20 s.
   - If the VLM times out, is unavailable, is off (`vlm.enabled`) or is over budget → the manual type grid. **Never a dead end.**
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
`label` is given **in both languages**, nominative, lowercase. Part boxes are normalized 0–1, from joint to tip, not overlapping. The output is validated with a Pydantic model. The prompt (`prompts/classify.md`, in English, currently v2) has a version header, and the version is stored in `alive_job.vlm_result`. Prompt rules:
- Pick the type by **function in the world**, not by drawing style.
- Anything unrecognizable → `doodle`.
- `unsafe` covers nudity, hate symbols and realistic violence against people.
- Drawings may contain handwriting in Russian or English; read it as a hint.

Accuracy is measured with `scripts/vlm_accuracy.py` over 81 drawings (target ≥ 80% top-1); last run: [`reports/vlm-accuracy.md`](reports/vlm-accuracy.md).

---

## 5. Data model (SQLite)

`app/schema.sql` is authoritative. Conventions:
- ids are TEXT (uuid4, or nanoid for canvas slugs)
- times are INTEGER unix ms
- JSON is stored as TEXT
- every enum gets a CHECK constraint
- `PRAGMA foreign_keys=ON`
- A database from another schema version (`PRAGMA user_version`) is refused at startup with a clear error. The one migration (`app/db.py`) takes version 2 to 3 (YooKassa → Robokassa): `payment` is renamed `payment_yookassa` and kept, `subscription.yk_payment_method_id` becomes an empty `parent_inv_id`, and everything else is untouched.

```sql
CREATE TABLE app_user (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, display_name TEXT,
  locale TEXT CHECK (locale IN ('ru','en')), created_at INTEGER NOT NULL);
CREATE TABLE session (id TEXT PRIMARY KEY, user_id TEXT REFERENCES app_user(id), nickname TEXT,
  locale TEXT, created_at INTEGER NOT NULL, last_seen_at INTEGER);
CREATE TABLE login_cookie (digest TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE REFERENCES session(id));
CREATE TABLE login_code (session_id TEXT PRIMARY KEY, email TEXT NOT NULL, locale TEXT, code_hash TEXT NOT NULL,
  expires_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);  -- one pending code per session
CREATE TABLE subscription (id TEXT PRIMARY KEY, user_id TEXT UNIQUE NOT NULL REFERENCES app_user(id),
  plan TEXT CHECK (plan IN ('free','plus')), status TEXT CHECK (status IN ('active','past_due','canceled')),
  current_period_end INTEGER, parent_inv_id INTEGER /* Robokassa invoice that renewals repeat */);
CREATE TABLE payment (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES app_user(id),
  inv_id INTEGER UNIQUE NOT NULL CHECK (inv_id > 0) /* Robokassa InvId */, plan TEXT NOT NULL,
  amount_kop INTEGER NOT NULL, currency TEXT NOT NULL,
  recurring INTEGER NOT NULL DEFAULT 0 /* sent with Recurring=true */,
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

-- Strokes: append-only and IMMUTABLE. Points in world coords; every stroke lies in one chunk.
CREATE TABLE stroke (id INTEGER PRIMARY KEY AUTOINCREMENT, canvas_id TEXT NOT NULL REFERENCES canvas(id),
  seq INTEGER NOT NULL, client_id TEXT NOT NULL, session_id TEXT, color TEXT NOT NULL, width REAL NOT NULL,
  points TEXT NOT NULL, chunk_x INTEGER NOT NULL, chunk_y INTEGER NOT NULL, created_at INTEGER NOT NULL,
  UNIQUE (canvas_id, seq), UNIQUE (canvas_id, client_id));
CREATE INDEX stroke_chunk ON stroke (canvas_id, chunk_x, chunk_y, seq);
-- A stroke's fate: append-only and IMMUTABLE. Forward only: (none) → consumed → fossil, or (none) → destroyed.
CREATE TABLE stroke_fate (canvas_id TEXT NOT NULL, seq INTEGER NOT NULL,
  fate TEXT NOT NULL CHECK (fate IN ('consumed','destroyed','fossil')), entity_id TEXT, at INTEGER NOT NULL,
  PRIMARY KEY (canvas_id, seq, fate), FOREIGN KEY (canvas_id, seq) REFERENCES stroke (canvas_id, seq));
-- Current state = latest fate for (canvas_id, seq); no row = static.
-- Triggers: BEFORE UPDATE / BEFORE DELETE ON stroke and ON stroke_fate → RAISE(ABORT);
-- BEFORE INSERT ON stroke_fate enforces the forward-only transitions.

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
  type TEXT, actors TEXT /* every entity the event names */, payload TEXT);
CREATE INDEX world_event_at ON world_event (at);
CREATE TABLE metric (key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0);
```

**Stroke insert:** `BEGIN IMMEDIATE` → dedupe on `(canvas_id, client_id)` (a resubmit returns the existing `seq`) → `seq = stroke_count + 1` → insert with `chunk_x/chunk_y` → increment → `COMMIT`. **No `await` inside.** Broadcast only after commit.

**Rules:**
- Effective role = owner ? `editor` : `max(link_role, member.role)`. If the canvas is frozen, every role is downgraded to `viewer`.
- The shared world (`kind=world`, created at startup if missing) gives `editor` to everyone.
- Robokassa's ResultURL is idempotent via `payment.inv_id UNIQUE`: only a `pending` payment can settle.
- Invoice numbers are ours: `max(last inv_id + 1, unix seconds)`, so they never repeat for the shop, even after a fresh database.

**Retention** (daily pass, `app/retention.py`):
- `alive_job` and raw VLM output: 30 days
- cutout and sprite sheet files: 7 days after death; the sprite row stays with its paths set to NULL. Built-in sprites are kept.
- `world_event`: 90 days
- sign-in codes: deleted on use or expiry
- strokes, fates and payments: never touched

Sprite files live in the data volume (`DATA_DIR/sprites/`) and are served by FastAPI with long cache headers.

**Backups:** `deploy/backup.py` takes an online SQLite backup, checks it, keeps 14 daily copies and mirrors `sprites/`; on the server a root cron line runs it daily inside the container.

---

## 6. Rig and bake (procedural sprites from the user's lines)

The rig splits the player's **strokes**, not the picture (`app/alive/parts.py`), then cuts each part into its own layer. The body keeps a 2 px overlap. No parts → a single layer. Rules for assigning strokes to parts, each from a case seen in real drawings:

- strokes the client cut at a chunk border are joined back first;
- the drawing is the largest group of touching lines (plus what lies inside it): captions never move and do not set the ground;
- legs reach the ground; overlapping leg boxes give each stretch of line to one leg; a leg stroke that leaves its box at the hip or the foot is a leg end to end (turns at the hip); walkers also get legs the VLM did not box (strokes hanging from the body to the ground);
- a flat run in a leg box is the belly; a wing or tail must end in its box; a separate leg or tail must start at the body; small closed loops (letters, eyes, spots) never move;
- wheels take only strokes inside their circle.

**Pivots:**
- leg: middle of the top edge (it turns at the hip)
- wing: the edge nearest the body centre (it turns at the shoulder)
- wheel: centre
- head: middle of the bottom edge
- tail: the side touching the body

| anim | frames | how |
|---|---|---|
| idle | 4 | Breathing: scaleY 1→0.97→1. **Plants:** wind sway, an X-shear by horizontal strips that grows with height (±4% of width). **Vehicles:** ±1 px shake |
| move | 8 | **Legs:** ±20° in counter-phase pairs, plus a 3% body bob (people swing legs). **Wings:** flap by turning at the shoulder. **Wheels:** +45°/frame; vehicles only spin wheels, planes shake. **Swim:** fish swim with the whole body, a sine wave across vertical strips. **No parts:** squash-and-stretch hop |
| act | 4 | **Eat:** tilt forward 10°. **Attack:** 8% lunge. **Explosive:** pulse plus a red tint. **Fire:** flame wobble |
| death | 6 | flip 180°, desaturate, fade to fossil grey |

- **Bake:** each frame is the bbox plus 16% padding, ≤ 384 px. The frames go into `sheet.png`, ≤ 2048×2048. The client flips the sprite horizontally based on movement direction.
- **Not rigged:** `water` (original drawing + client ripple) and `sky` (original drawing, drifting). The water outline is rays from the centre to the farthest painted pixel in 64 directions, not marching squares: a lake is usually drawn as a loop, and its inside must count.
- **The manifest is the contract.** Any future sprite source (e.g. AI sprites) must output the same format:
```json
{ "frameW": 212, "frameH": 168, "pivot": [106, 160], "facing": "right",
  "anims": { "idle": {"row":0,"frames":4,"fps":4}, "move": {"row":1,"frames":8,"fps":10},
             "act":  {"row":2,"frames":4,"fps":8}, "death": {"row":3,"frames":6,"fps":8} } }
```

Walk cycles can be reviewed without spending money: `scripts/vlm_accuracy.py --from-json … --sheets DIR` replays saved VLM answers and saves each sheet.

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

**Performance:**
- Positions and velocities live in numpy arrays and move vectorized.
- **Staggered decisions:** each entity re-decides at ~2 Hz (one fifth of entities per tick), and immediately on threat.
- Only active chunks tick.
- **Targets:** p95 tick ≤ 60 ms with **2 000 active entities**; client 60 fps with 200 visible; control latency ≤ 150 ms. Measured with `scripts/loadtest.py`; last run: [`reports/loadtest.md`](reports/loadtest.md).
- **5 Hz fallback:** if the tick target isn't met, the sim drops to 5 Hz (the client interpolates) and goes back to 10 Hz once a 100-tick window's p95 is under 30 ms. The sim logs its tick times every `SIM_STATS_INTERVAL_S` (60 s).

**States:** `wander, seek_food, eat, seek_water, drink, rest, mate, flee, hunt, attack, dead`. `flee` pre-empts everything within 150 px.

**Chunks:**
- **Active** = a client is subscribed to it or to a neighbour, or it contains a lit fuse or fire.
- Sleeping chunks freeze.
- On wake, an analytic catch-up runs for `Δt = min(elapsed, 10 min)` (flag `sleep_catchup`); positions don't change. Deterministic test: `tests/test_sim_catchup.py`.

**Persistence:** the sim sends snapshots of dirty entities every 5 s and on sleep, and batched events every 1 s; **main writes them**.

**Network** (sent by main):
- `world:snapshot` on subscribe.
- `world:delta`: **one message per client per tick**, `{tick, chunks: [{cx, cy, upd, gone, enter}]}`, holding every watched chunk that changed. Each chunk is encoded once and joined per client (`Realtime.emit_deltas`); per-chunk messages cost too much CPU at 50 clients. socketio's binary scan is off (we never send binary).
  - `upd` rows are `[ref, x, y, st, anim, hp%]`, changed fields only, positions to 0.1 px, optionally ending with a `view` object (scale, bites, stock, fuse, raining, bound).
  - `ref` is a small number main gives each entity (kept across sim restarts); every full copy of an entity carries it. `gone` lists refs.
  - `enter` holds full entities that walked into the chunk.
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
| GET `/api/me` | anyone | → `{user?, plan, ads, locale, quota:{used,limit}, subscription, adBlocks}` |
| PUT `/api/me/locale` | anyone | `{locale:"ru"\|"en"}` → 204 |
| POST `/api/auth/code` | anyone | `{email}` → 204 (≤ 3 mails / 10 min / address; mail in the session locale) |
| POST `/api/auth/verify` | the session that asked for the code | `{code}` → 204 + Set-Cookie (httpOnly, SameSite=Lax); links the anonymous session to the user |
| POST `/api/auth/logout` | logged in | → 204 |
| GET `/api/canvases` | logged in | → my private worlds |
| POST `/api/canvases` | Plus | `{title}` → 201 `{id, kind:"private"}` (≤ 5 worlds, else `WORLD_LIMIT`) |
| GET `/api/canvases/{id}` | role ≥ viewer | → `{id, kind, title, role, owner, frozen, bounds, spawn:{x,y}, limits, palette}` |
| GET `/api/canvases/{id}/chunks/{cx}/{cy}?afterSeq=` | role ≥ viewer | → `{strokes[], fates[], entities[], fossils[], lastSeq, tick}` |
| POST `/api/canvases/{id}/alive` | editor | `{lasso:[[x,y],…]}` → 202 `{jobId, quota}` |
| GET `/api/alive/{jobId}` | author | → `{status, type?, entityId?, error?}` |
| POST `/api/alive/{jobId}/choice` | author | `{type}` → 200 |
| GET / PUT `/api/canvases/{id}/access` | owner | `{linkRole}` |
| POST `/api/canvases/{id}/members` | owner | `{email, role}` → 201 (invite mail) |
| PATCH / DELETE `/api/canvases/{id}/members/{userId}` | owner | `{role}` / — |
| GET `/api/me/events?since=` | anyone | events (since unix ms) whose actors include an entity the caller's session or account brought to life, or a child of one; 100 per page, `next` for paging |
| POST `/api/billing/checkout` | logged in | `{plan:"plus_month"\|"plus_year"}` → `{paymentUrl}` (a signed Robokassa link) |
| GET / POST `/api/billing/result` | Robokassa | ResultURL (form fields `OutSum`, `InvId`, `SignatureValue`, `Shp_*`…) → `text/plain` `OK<InvId>`. **Trusted only if signed with password #2** + IP allowlist (`result_ips` in `config/billing.yaml`); bad signature → 400 |
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
| s→c | `canvas:frozen` | `{frozen}`; clients reload | |

**Roles:**
- **viewer:** watches.
- **player:** can pet, push, drag and drive; no drawing and no ALIVE.
- **editor:** everything.

Draggable types are `food`, `explosive`, `remote` and `doodle`. Roles are checked **server-side** on every call and event. Losing access disconnects the socket (`Realtime.recheck`).

**Reconnect:** backoff 1, 2, 4 s …; the client's outbox resends unacknowledged strokes (deduped by `clientId`). Each subscribed chunk resyncs from a **watermark**, the `lastSeq` of its last fetch (`afterSeq=watermark`), since `seq` is per canvas and sparse within a chunk; live events above it are applied idempotently. A socket error `NOT_JOINED` tells the client to resubscribe.

### Error envelope
`{"ok": false, "error": {"code", "message", "field", "retryable", "details"}}`
- `message` is localized to the request locale (the session/user locale, else `Accept-Language`).
- **The client renders its own localized text from `code` + `details`.** `message` is only a fallback.

Codes:
- VALIDATION_FAILED 400, CODE_INVALID 400, CODE_EXPIRED 410, UNAUTHENTICATED 401, FORBIDDEN_ROLE 403, PAYMENT_REQUIRED 402
- CANVAS_FROZEN 423, NOT_FOUND 404, QUOTA_EXCEEDED 429, RATE_LIMITED 429
- SELECTION_EMPTY 422, SELECTION_TOO_LARGE 422, SELECTION_MULTIPLE 422, SELECTION_BUSY 409, STROKES_ALREADY_ALIVE 409
- ENTITY_LIMIT 409, WORLD_LIMIT 409, UNSAFE_CONTENT 422, VLM_TIMEOUT / VLM_UNAVAILABLE (→ manual type grid, not shown as an error)
- CHOICE_EXPIRED 410, CONTROL_TAKEN 409, PAYMENT_FAILED 402, PAYLOAD_TOO_LARGE 413, INTERNAL 500
- NOT_JOINED (socket only)

`QUOTA_EXCEEDED`'s `details` add `more` (anonymous: what signing in adds) or `plusLimit` (free), for the plural hint.

`app/errors.py` is authoritative for codes and statuses.

---

## 9. Ads, adblock gate, plans, auth, billing

- **Show ads** when the viewer's plan is free (including anonymous) **and** `ads.enabled`. Ads depend on the **viewer's** plan, never the host's.
- **Layout:** desktop (≥ 1100 px wide) gets a rail on either side of the canvas (240 px, 324 px from 1500 px), each with **two РСЯ slots stacked** — four in all, the side block (`ADS_BLOCK_SIDE`) shown four times with `pageNumber` 1–4 and its own container each. Narrow screens get one block (`ADS_BLOCK_BOTTOM`) in a 100 px strip at the bottom. The slots are sized by CSS alone and laid out before the world loads, so a banner arriving, failing or being oversized **never moves or resizes the canvas**. The choice of layout is made once when the world opens.
- **Adblock detector (client, `web/src/ads.js`):**
  1. Create `window.yaContextCb`, then load the РСЯ loader with `onerror` (the loader is loaded only for viewers who get ads).
  2. Add a bait `div.adsbox.ad-banner` (1×1).
  3. After 1.5 s, blocked = the script failed **or** the bait is hidden. **An empty ad fill is NOT a block.**
  4. If blocked: don't mount the canvas, don't connect the socket, show the gate.
  5. Metrics: `adblock_gate_shown`, `adblock_gate_passed`, `plus_clicked`.
- **Private worlds:** only the host needs Plus; guests on any plan can join.
- **Freeze:** if the owner's subscription is `past_due` for more than 3 days, or `canceled` after the period end → `frozen=1`. The sim pauses and everyone becomes a viewer. Paying unfreezes immediately. Never delete. With `billing.recurring` off, a subscription whose period ends becomes `past_due`, so its worlds freeze after the 3-day grace.
- **Billing (Robokassa test mode, behind `app/billing/gateway.py`; overview and history in [`billing.md`](billing.md)):**
  1. `checkout` saves a `pending` payment with a new invoice number and returns a link to `https://auth.robokassa.ru/Merchant/Index.aspx` with `MerchantLogin`, `OutSum` (`299.00`, or `2490.00` for a year), `InvId`, `Description` and `Culture` in the user's language, `IsTest=1`, the fiscal `Receipt` (`config/billing.yaml`), `Recurring=true` when `billing.recurring` is on, and `SignatureValue = hash(MerchantLogin:OutSum:InvId[:Receipt]:Password#1)`. The Receipt is compact JSON, URL-encoded once in the signature and again in the link. The hash is the shop's (`ROBOKASSA_HASH`, MD5 by default).
  2. The user pays on Robokassa's page and comes back to `/billing/return` (SuccessURL; it only shows the state) or `/plans` (FailURL).
  3. Robokassa posts ResultURL → the signature `hash(OutSum:InvId:Password#2[:Shp_…])` is checked, `OutSum` must equal the invoice's amount (otherwise it is `canceled`) → payment succeeded, subscription active, `period_end` extended from the later of now and the current end → answer `OK<InvId>`. Robokassa's status API (OpStateExt) does not answer for test payments, so the signature is the proof.
  4. When `billing.recurring` is on, a period that ends marks the subscription `past_due` and charges another month by `POST https://auth.robokassa.ru/Merchant/Recurring` (`InvoiceID` new, `PreviousInvoiceID` = the first invoice paid with `Recurring=true`, signed like the link). Robokassa reports the money at ResultURL, which makes the subscription active again; a refused charge cancels that invoice and the subscription stays `past_due`. The pass runs every minute (logged once a day at 03:00 MSK); a period is charged at most once.
  5. Prices are **always in rubles**, in either UI language. Fiscal receipts are Robokassa's (the `Receipt` parameter).
- **Auth:**
  - Sign-in by a 6-digit code sent by e-mail (`secrets.randbelow`), stored as SHA-256 of `session:code`, 15 min TTL, single use, 5 wrong guesses void it. A code works only in the browser session that asked for it; a new request replaces it.
  - The letter is text + HTML: a greeting, the code, what to do with it, what to do if you didn't ask. A named sender, `Date` and `Message-ID` headers. Without SMTP settings, mail is printed to the server log (`ConsoleMailer`).
  - On login, the anonymous session is linked to the user; its strokes, entities and quota stay with it. The session cookie is rotated on sign-in and stored hashed (`login_cookie`).
  - Only e-mail + locale are stored (the only personal data); a privacy notice and consent are shown at login, in both languages.

## 9a. Languages: Russian + English

- **Default:** `ru` if `navigator.language` starts with `ru`, otherwise `en`. A **RU / EN switch** sits in the top bar and the profile. It's saved in localStorage (wrapped in try/catch), in the session, and in `app_user.locale`.
- **Client catalogs:** `web/src/i18n/ru.json` and `en.json` with **identical keys**. Plurals use the built-in `Intl.PluralRules` (Russian has one/few/many: «1 оживление, 2 оживления, 5 оживлений»).
- **A test fails** if a key is missing in either catalog, or if a Cyrillic or English user-facing string literal appears outside the catalogs.
- **Server catalogs** (`app/i18n/`) hold e-mails and error `message` fallbacks, with the same rule.
- **Object labels:** from the VLM's `label.{ru,en}`. Name tags avoid declension: «корова · Саша» / "cow · Sasha".
- **Never** concatenate translated fragments. Use whole sentences with placeholders.
- `<html lang>` and the document title follow the locale.

Core keys (the catalogs are authoritative and hold many more). Plural keys hold one/few/many/other
forms in RU and one/other in EN; the table shows the RU one/few/many endings.

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
| control.taken.anon | Сейчас рулит кто-то — ещё {s} с | Someone is driving — {s} s left |
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
| err.QUOTA_EXCEEDED.hint.anon (plural on {n}) | Войди — и получишь ещё {n} оживление / оживления / оживлений | Sign in to get {n} more |
| err.QUOTA_EXCEEDED.hint.free (plural on {n}) | «Плюс»: {n} оживление / оживления / оживлений в день | Plus: {n} a day |
| err.QUOTA_EXCEEDED.hint.plus | Возвращайся завтра | Come back tomorrow |
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

**Hand tool:** click pets (or claims a remote), double click pushes, drag carries.

**Routes:**
- `/` — landing, RU/EN
- `/w` — shared world
- `/p/{slug}` — private world
- `/plans`
- `/billing/return` — Robokassa's SuccessURL
- legacy `/c/{id}` redirects to `/w`

---

## 11. Out of scope and future work

**Out of scope:** PvE/PvP/«театр» modes, timelapse/clip/PNG export, report/ban tools, ✨ packs, Telegram Mini App, multi-node scaling (one Uvicorn worker and one sim process are a hard limit), A* pathfinding, migrating old databases.

**Possible later, and what the design already allows for:**
- **Custom objects from a text description** (paid): an LLM writes a component config in the `config/archetypes.yaml` format (§3).
- **AI-generated sprites:** must produce the manifest format in §6.

---

## 12. Acceptance scenarios (two browser clients; once in RU and once in EN)

`web/e2e/scenarios.spec.js` (`npm run e2e`) automates parts of 1, 5, 6, 8, 9 and 14 (with a
lower quota for 9). The rest are walked by hand or covered by unit tests. What was last walked,
and what wasn't: [`reports/scenario-run.md`](reports/scenario-run.md) and [`status.md`](status.md).

1. An anonymous user opens `/w` and lands near activity. They pan and zoom across a big world, draw a cow, a pond and an apple tree, and press **ОЖИВИ** (or **ALIVE** in EN) on each. Both clients see the goo and then the objects, with name tags in each client's own language.
2. The cow grazes or eats apples and drinks from the pond. With no food it dies and becomes a grey fossil where it died. The stroke rows are untouched; only `stroke_fate` rows were added.
3. A wolf hunts the cow; the cow flees; a drawn warrior attacks the wolf.
4. Two fed cows have a calf (a smaller copy of the same drawing). Caps hold (30 min soak: `tests/test_sim_soak.py`).
5. A grenade counts down from 20 s, explodes, and destroys entities and static strokes in the radius (as `destroyed` fates). Fire next to a bomb detonates it instantly. Fire never burns static strokes.
6. A car does nothing until a remote is drawn nearby. A player drives it with WASD or the joystick; the other client sees `control.taken`.
7. A cloud rains: the puddle grows and a fire goes out.
8. An ambiguous drawing → chooser. With the VLM down (fake adapter) → manual grid, and it still comes alive.
9. The 11th ALIVE for an anonymous user that day → `QUOTA_EXCEEDED` with the sign-in hint, in the right language and with correct Russian plurals.
10. Sign in with an e-mailed code → buy Plus in Robokassa test mode (rubles in both languages) → ads disappear → create a private world → share it with role Player to a free account. The guest can pet and drive but not draw or ALIVE, and still sees ads.
11. Expire the host's subscription → the world freezes for everyone → pay → it unfreezes.
12. With `ads.enabled` and an adblocker on as a free user → the gate; turn it off → retry → the canvas opens. An empty ad fill doesn't trigger the gate.
13. Killing the sim process mid-game → it restarts and the world resumes (≤ 5 s lost).
14. Switching RU ↔ EN changes every visible string. The missing-key test passes. The load test meets §7; `docker compose up --build` works from a clean clone; all tests are green.
15. A free viewer on desktop sees four ad slots, two stacked on each side of the canvas; banners arriving late (even oversized ones) don't move the canvas. A Plus viewer sees no ad space; a narrow screen gets one fixed strip at the bottom.
