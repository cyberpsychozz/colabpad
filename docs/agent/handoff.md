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

## Done (one commit per task, tests with each)

| Task | State |
|---|---|
| T0–T6, T8–T12, T15–T18 | done |
| T24 | client and server catalogs, missing-key and stray-Cyrillic tests, RU/EN switch, error envelope. Not done: full landing copy, e-mails |
| T23 | daily quota per Moscow day, refunds, 1 ALIVE per 5 s are in T9. Not done: free/Plus limits depend on T19/T21 |

## Not done

- **T7** accuracy run over 60+ drawings: needs a real VLM key.
- **T13** reproduction works and is tested; the 30-minute soak for caps is not written.
- **T14** sleep catch-up is implemented (`World._catch_up`, `life.catch_up`, each brain's
  `catch_up`) but has no test yet.
- **T19** backend implemented: localized SMTP links, single-use 15-minute tokens, session linking, quota transfer, logout and account data in `/api/me`. Login UI and bilingual privacy consent are pending. Mail delivery is tested with a fake mailbox; configure `SMTP_*` and `PUBLIC_URL` for a real sender.
- **T20** private worlds and roles. `Realtime.role()` in `app/realtime/gateway.py` is the one
  place to extend; today the shared world is editor for all and other canvases use `link_role`.
- **T21** ЮKassa, freeze. `canvas.frozen` is honoured everywhere; nothing sets it yet.
- **T22** ad slots and the adblock gate.
- **T25** load test script, **T26** the §12 scenarios, **T27** `docs/agent/deploy.md`.

## Not verified

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
- Hand tool: click pets (or claims a remote), double click pushes, drag carries.

## Open questions for the human

- Playwright for the §12 scenarios: approved or not?
- A VLM key and budget for T7.
