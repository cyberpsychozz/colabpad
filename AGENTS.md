# Agent guide: CollabPad «Живой холст» / "Living Canvas"

**collabpad.ru** is a shared play world where drawings come alive: people draw (no undo), lasso
a drawing and press **ОЖИВИ** / **ALIVE**; a VLM classifies it, the server rigs a sprite from the
user's own lines, and the object lives in a server-authoritative world simulation that every
viewer sees. Python 3.12 + FastAPI + python-socketio + SQLite on the server, vanilla JS + Vite +
Canvas 2D on the client, RU + EN.

## Read first

| Doc | What |
|---|---|
| [`docs/spec.md`](docs/spec.md) | **the product spec**, describing the system as built: types, interaction matrix, pipeline, schema, contracts, i18n, acceptance scenarios. Code cites it as "spec §N". **A change to behaviour, a contract or the schema updates the spec in the same commit.** |
| [`docs/status.md`](docs/status.md) | what is not done, not verified, and open questions. Keep it current |
| [`docs/development.md`](docs/development.md) | running locally (Docker or `.venv`) and the checks |
| [`docs/deploy.md`](docs/deploy.md) | production deploy, for the human only |
| `docs/reports/` | load test, VLM accuracy and browser scenario runs |
| `docs/background/` | the original TZ and business case (Russian, .docx) |

If anything is unclear or contradicts reality, **stop and ask the human**.

## Rules

1. **No undo is sacred.** `stroke` and `stroke_fate` rows are never updated or deleted; DB
   triggers enforce it. A stroke's fate (consumed, destroyed, fossil) is a new `stroke_fate` row.
2. **Exactly one Uvicorn worker** and **one simulation process** (started by the app). The main
   process is the only DB writer. The server is the only source of truth for the world.
3. **Never touch production.** No ssh/scp/rsync, never run any deploy script, and **never
   push to the `deploy` branch**: a push there deploys to production through GitHub Actions
   (`.github/workflows/deploy.yml`). Deploying and wiping the production DB are human steps,
   written down in `docs/deploy.md`. Deploys use **Docker Compose only**.
4. **No real money, no live keys.** YooKassa in test mode only; keys only from env vars
   (add `.env.example` entries, never commit secrets); the VLM has a daily budget cap.
5. **Bilingual.** Every user-facing string goes through the catalogs (`web/src/i18n/`,
   `app/i18n/`) in Russian and English with identical keys; a test fails otherwise. Never
   concatenate translated fragments. Code, comments, logs and identifiers are English.
6. **Ask before adding a dependency.**
7. **Every change comes with tests, and every test passes.** Server behaviour: `pytest` in
   `tests/`. Client logic: `node --test` next to the code (`web/src/**/*.test.js`). Anything a
   player sees or does in the browser, including what other clients see: a Playwright scenario
   in `web/e2e/` (the acceptance scenarios are spec §12). A bug fix starts with a test that
   fails without the fix. Before each commit, `pytest -q`, `npm test` and `npm run e2e` are
   green (commands in `docs/development.md`); the deploy workflow runs all three and deploys
   nothing if one fails. For UI work, also look at it yourself in two browser clients.
8. **Commits:** small, on a feature branch, no AI attribution lines in commits or PRs.
