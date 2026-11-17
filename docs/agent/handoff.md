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

Checks: `env -u PYTHONPATH .venv/bin/python -m pytest -q` and `npm test` in `web/`.
Stop a running dev server before running pytest; two timing-sensitive tests dislike the load.

ALIVE without a key shows the manual type grid (the author picks the type). For automatic
recognition put an OpenRouter key in `.env` as `VLM_API_KEY`; models and the $5/day cap are in
`config/vlm.yaml`. The real VLM path has only been tested against a mocked HTTP transport.

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

Local development without SMTP: mail (sign-in links, invitations) is printed to the server log
(`ConsoleMailer`). Without YooKassa test keys checkout answers `PAYMENT_FAILED`; a key that does
not start with `test_` is refused on purpose.

## Not done

- **T7** accuracy run over 60+ drawings: needs a real VLM key and budget.
- **T25** load test script (2 000 entities, 50 clients).
- **T26** the §12 scenarios end to end, balance.
- **T27** `docs/agent/deploy.md` (must mention: `SMTP_*`, `ADS_BLOCK_*`, YooKassa webhook URL
  `/api/billing/webhook`, uvicorn already runs with `--proxy-headers` so the IP allowlist sees
  the real client address).
- Retention job (§5), `GET /api/me/events`.
- After sign-in the callback always redirects to `/w`, not back to the world the user came from.

## Not verified

- A real YooKassa test shop (only the fake and a mocked HTTP transport), real SMTP delivery,
  and the gate with a real ad blocker (unit-tested; in the browser only "no blocker, empty
  fill, world opens" was seen).

- `docker compose up --build`: the Docker daemon was not running. The Dockerfile now installs
  `libfontconfig1 libgl1 libegl1` for skia-python and copies `config/` and `prompts/`.
- Driving in the browser (remote, WASD, stick): server and store logic are tested, the overlay
  was not clicked through.
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

## Open questions for the human

- Playwright for the §12 scenarios: approved or not?
- A VLM key and budget for T7.
