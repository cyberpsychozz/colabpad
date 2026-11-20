# Handoff: state of the Living Canvas rebuild

Read `AGENTS.md` first (the brief), then `docs/agent/step0-notes.md`. This file says what is
done, what is not, and what the next agent should know. Branch: `living-canvas`, not pushed.

## Run it

```bash
./start.sh
```

Then open http://127.0.0.1:8000/w. `.venv` and `web/node_modules` must exist (see `LOCAL.md`).
`start.sh` builds the frontend only if `app/static` is missing; after changing anything in
`web/`, run `npm run build` there.

Checks: `env -u PYTHONPATH .venv/bin/python -m pytest -q` and `npm test` in `web/`. The §12
scenarios in two browsers: `npm run e2e` in `web/` (needs Google Chrome installed; builds the
frontend, starts its own server on port 8030 with a throwaway database and the VLM off).
The VLM's accuracy: `scripts/vlm_accuracy.py` (spends real money, about $0.25 a run; see
`docs/agent/vlm-accuracy.md`). Load: `scripts/loadtest.py`.
Stop a running dev server before running pytest; two timing-sensitive tests dislike the load.

ALIVE without a key shows the manual type grid (the author picks the type). For automatic
recognition put an OpenRouter key in `.env` as `VLM_API_KEY`; models and the $5/day cap are in
`config/vlm.yaml`. The real VLM path has only been tested against a mocked HTTP transport.
`VLM_PROXY_URL` sends the VLM calls (and only those) through an HTTP proxy, e.g. a userspace
Tailscale instance using an exit node (`deploy/systemd/collabpad-egress.service`, the Compose
`egress` profile, `docs/agent/deploy.md` §5a). The app's HTTP clients ignore `HTTPS_PROXY`.

## Done (tests with each)

| Task | State |
|---|---|
| T0–T6, T8–T18 | done (T13 soak: `tests/test_sim_soak.py`; T14: `tests/test_sim_catchup.py`) |
| T19 | magic-link login, session linking, logout, `PUT /api/me/locale`, login dialog with consent in RU/EN |
| T20 | private worlds (`/p/{slug}`), roles on every REST route and socket event, share dialog, invite mail |
| T21 | YooKassa behind `app/billing/gateway.py` (fake in tests), checkout, webhook (re-fetch + IP allowlist), cancel, renewal, freeze/unfreeze, `/plans`, `/billing/return` |
| T22 | ad slots, adblock detector and gate (`web/src/ads.js`, `web/src/ui/gate.js`) |
| T23 | quotas per Moscow day, refunds, rate limits, free/Plus limits |
| T24 | catalogs, switch, locale saved in session and account, e-mails. Not done: fuller landing copy |
| T25 | `scripts/loadtest.py` (real server, 2 000 entities, 50 WebSocket clients); report in `docs/agent/loadtest.md` |
| T26 | §12 walked in two browser clients (one RU, one EN), see "Scenario run" below |
| T27 | `docs/agent/deploy.md`; backups mirror `sprites/`; systemd `KillMode=mixed` |
| T7 | 81 drawings (`scripts/drawings.py`), real VLM run: 77/81 = 95% top-1, RU/EN handwriting 14/14 (`docs/agent/vlm-accuracy.md`) |
| §12 e2e | `web/e2e/scenarios.spec.js` with Playwright: 5 scenarios, both languages |
| §5 / §8 extras | daily retention pass (`app/retention.py`), `GET /api/me/events?since=`, sign-in returns to the world it started from |

Local development without SMTP: mail (sign-in links, invitations) is printed to the server log
(`ConsoleMailer`). Without YooKassa test keys checkout answers `PAYMENT_FAILED`; a key that does
not start with `test_` is refused on purpose.

## Not done

- Balance tuning beyond the brief's defaults (part of T26): nothing was changed.
- Sprites already baked in a world before the rig rewrite keep their old (torn) animation;
  only new ALIVEs get the new rig. Production starts from an empty database, so this only
  matters for local worlds.

## Animation (rig) after the T7 review

The rig splits the player's strokes, not the picture (`app/alive/parts.py`). Reviewed by eye
on the local world's drawings and on all 81 T7 drawings with the real VLM's boxes
(`scripts/vlm_accuracy.py --from-json … --sheets DIR` replays saved answers for free and saves
each walk cycle). The rules, each from a case seen there:

- strokes the client cut at a chunk border are joined back first;
- the drawing is the largest group of touching lines (plus what lies inside it): captions
  never move and do not set the ground;
- legs reach the ground; overlapping leg boxes give each stretch of line to one leg; a leg
  stroke that leaves its box at the hip or the foot is a leg end to end (turns at the hip);
  walkers also get legs the VLM did not box (strokes hanging from the body to the ground);
- a flat run in a leg box is the belly; a wing or tail must end in its box; a separate leg
  or tail must start at the body; small closed loops (letters, eyes, spots) never move;
- wheels take only strokes inside their circle; vehicles only spin wheels (planes shake),
  fish swim with the whole body, people swing legs, wings flap by turning at the shoulder.

## Scenario run (T26, 2026-11-18)

Two clients in the built-in browser on a throwaway database (`127.0.0.1` and `localhost` give
two sessions), client A in EN then RU, client B in RU. The VLM key was blanked on purpose, so
every ALIVE went through the manual grid (§12.8). Seen working:

- strokes in both clients; goo in both (B's stages in Russian); the grid; the 20 s auto-pick
  (falls back to the scribble without a VLM); name tags in each client's own language;
- the ALIVE 5 s rate limit message; the 11th ALIVE → `QUOTA_EXCEEDED` with the sign-in hint,
  now with the Russian plural («ещё 10 оживлений»), `<html lang>` and title following the locale;
- a car and a remote: B claims the remote, drives with held WASD (lease extends), A gets
  "Someone is driving — 49 s left", B lets go;
- a grenade: countdown visible, explosion after 20 s, the stroke 112 px from the centre of a
  118.5 px blast got `destroyed`, the one at 120 px stayed static;
- `kill -9` of the simulation process: restarted within a second, world carried on;
- magic-link sign-in (link from the log), Plus granted in the DB (no YooKassa test keys here),
  private world created, link access Player: the guest sees only the hand tool, can pet
  (and the server refuses `stroke:add` and ALIVE with `FORBIDDEN_ROLE`);
- subscription expired → both clients reload into the frozen world with `canvas.frozen` and
  the owner as viewer; reactivated → unfrozen (the minute sweep picked both up).

`docker compose up --build -d --wait` from a clean clone (own project name, port 8020): healthy,
`/w` served, built-in sprites baked with skia inside the image, simulation process running,
clean stop. It had been failing at `npm test` in the image (the catalog test reads
`app/errors.py` and `config/archetypes.yaml`); the Dockerfile now copies those two files.

Not walked: §12.2/3/4/7 (behaviour over minutes: unit-tested per ● cell), §12.10's payment
itself and §12.12's gate (no test shop keys, no ad blocker in the pane).

Fixed on the way: a guest without a name made "someone is driving" start in lower case (now a
whole sentence of its own), the quota hint had no plural forms, and the ALIVE panel shrank
into a narrow column near the right edge.

## Not verified

- A real YooKassa test shop (only the fake and a mocked HTTP transport), real SMTP delivery,
  and the gate with a real ad blocker (unit-tested; in the browser only "no blocker, empty
  fill, world opens" was seen).

- Driving with the on-screen stick (touch); WASD was driven in the browser.
- Entity animation frames with real part boxes (legs, wheels): the rig is unit-tested; in the
  browser only drawings typed through the manual grid (no parts, so they hop) were seen.

## Decisions taken that differ from, or add to, the brief

- Strokes are cut at chunk borders on the client, and the server rejects a stroke that spans
  two chunks. Per-chunk resync uses a watermark (`lastSeq` of the last fetch), not a contiguous
  prefix. Both approved by the human.
- World size 19456×11264 (19×11 chunks), in `config/limits.yaml`, re-applied at each startup.
- Zoom-out is limited so the viewport never needs more than 12 chunks.
- A database from another schema version is refused with a clear error; never migrated.
- Water outline: rays from the centre to the farthest painted pixel (64 directions), not
  marching squares, because a lake is usually drawn as a loop and its inside must count.
- Fossils are served and drawn by where the entity died (`fossils` in the chunk response and a
  `world:event` of type `fossil`), since that can be a different chunk from the strokes.
- A campfire burns until doused; only wild fire burns out after 20 s. Burning a plant or house
  is a child fire entity on it; the host becomes a fossil when that fire burns out.
- Fruit, wild fish and flames use built-in sprites (`app/alive/builtin.py`).
- Population caps are checked when something is born or spawned; walking between chunks can
  put more than 8 of a label in one chunk.
- A warrior deals 20 per think so he narrowly beats a lone wolf.
- `world:delta` has an extra `enter` list with full entities that walked into the chunk, and a
  row may end with a `view` object (scale, bites, stock, fuse, raining, bound).
- `NOT_JOINED` is a socket-only error code that tells the client to resubscribe.
- Person on plant (a ● cell the brief does not describe): a hungry person shakes a fruit tree.
- With `billing.recurring` off, a subscription whose period ends becomes `past_due` and its
  worlds freeze after the 3-day grace; `canceled` freezes at period end.
- Signed-in sessions use a rotated cookie stored hashed in `login_cookie` (added table).
- `/api/me` also returns `subscription` and `adBlocks`; the canvas response has `owner`;
  `GET /api/canvases` lists my worlds; `WORLD_LIMIT` is an added error code.
- Losing access disconnects the socket (`Realtime.recheck`); `canvas:frozen` makes clients reload.
- Hand tool: click pets (or claims a remote), double click pushes, drag carries.
- `world:delta` is one message per client per tick: `{tick, chunks: [{cx, cy, upd, gone,
  enter}]}`, each chunk encoded once and joined per client (`Realtime.emit_deltas`).
  Per-chunk messages made main spend its time compressing ~6 000 sends a second at 50
  clients (control latency 240–450 ms). socketio's binary scan is off (`TextPacket`).
- The 5 Hz fallback goes back to 10 Hz once a 100-tick window's p95 is under 30 ms; the
  simulation logs its tick times every `SIM_STATS_INTERVAL_S` (60 s).
- Retention keeps built-in sprites and never touches strokes, fates or payments; an expired
  sprite keeps its row with the paths set to NULL.
- `/api/me/events?since=` (unix ms) lists events whose actors include an entity the caller's
  session or account brought to life, or a child of one; 100 per page, `next` for paging.
  World events now store every entity they name (`actors`).
- The magic link carries `next` (only `/w`, `/plans` or `/p/{slug}`) to land where sign-in
  started.
- `world:delta` rows and `gone` lists name entities by `ref`, a small number main gives each
  entity (kept across simulation restarts); every full copy of an entity carries it.
- The VLM gets what is left of the 25 s hard limit minus 5 s for rigging; past that the author
  gets the type grid. After a timeout the adapter goes straight to the fallback model.
- The classify prompt is v2: part boxes from joint to tip, not overlapping.
- The out-of-quota error's `details` add `more` (anonymous: what signing in adds) or
  `plusLimit` (free), for the plural hint.

## Open questions for the human

None at the moment. Answered: Playwright approved (used with the installed Chrome, no
browser download); T7 run with the key in `.env` ($0.24); short entity handles done.
