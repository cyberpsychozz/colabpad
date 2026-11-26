# Разработка и локальный запуск

Два способа: Docker (ничего, кроме Docker, не нужно) или напрямую из `.venv` (удобнее для
разработки). Выкладка на сервер — в [`deploy.md`](deploy.md).

## Docker

Нужны Docker Engine и Docker Compose. Из корня проекта:

~~~bash
docker compose up --build -d --wait
~~~

Открой http://127.0.0.1:1744/w. Состояние, логи, перезапуск, остановка:

~~~bash
docker compose ps
docker compose logs --tail=100 app
docker compose restart app
docker compose down
~~~

Эти операции сохраняют том `collabpad_data`. Переименование проекта Compose создаёт другой
том.

Если порт 1744 занят, используй другой порт с соответствующим `CORS_ORIGINS` (и в
последующих командах тоже; для постоянной настройки запиши их в `.env`):

~~~bash
COLLABPAD_PORT=8080 CORS_ORIGINS=http://localhost:8080,http://127.0.0.1:8080 docker compose up --build -d --wait
~~~

### Как устроен образ

- `Dockerfile` собирает интерфейс в стадии Node.js (и прогоняет клиентские тесты,
  включая общую с Python фикстуру `tests/golden/choreography.json`),
  зависимости Python ставит в отдельной стадии. В итоговом образе — Python, приложение,
  собранный интерфейс и `deploy/backup.py`; Node.js и исходники фронтенда туда не попадают.
- Настройки берутся из окружения или `.env` (пример — `.env.example`). Игровые настройки и
  флаги (`config/*.yaml`) копируются в образ при сборке.
- Папка данных в контейнере — `/var/lib/collabpad` (`DATA_DIR`): база `collabpad.db` и
  спрайты `sprites/`, в одном томе.
- Симуляция мира — отдельный процесс внутри того же контейнера. В Uvicorn всегда один
  worker; больше одной реплики приложения запускать нельзя (комнаты, аренда управления и
  симуляция живут в памяти одного процесса).
- Процесс работает от UID/GID 10001, корневая ФС только для чтения, порт опубликован на
  `127.0.0.1`. `.dockerignore` пропускает только то, что нужно сборке.
- Healthcheck проверяет `/api/health`.

### Резервная копия

~~~bash
docker compose exec app python deploy/backup.py
docker compose cp app:/var/lib/collabpad/backups ./backups
~~~

Утилита использует SQLite Backup API, проверяет целостность, хранит 14 последних дневных
копий и зеркало `sprites/` в `backups/sprites/`. Папка `backups/` исключена из Git.

### Перенос базы из запуска без Docker

1. Останови локальный сервер и Docker-приложение. Сделай онлайн-копию исходной базы:

   ~~~bash
   python3 deploy/backup.py --db data/collabpad.db --directory backups
   ~~~

2. Импортируй файл в новый, ещё пустой том (`YYYY-MM-DD` — дата из имени файла). Если
   Docker-версия уже запускалась, добавь `-p collabpad-migrated` сразу после
   `docker compose` в этой и всех дальнейших командах: так получится новый том.

   ~~~bash
   docker compose run --rm --no-deps -T app python -c 'import shutil,sys; from pathlib import Path; target=Path("/var/lib/collabpad/collabpad.db"); stream=target.open("xb"); shutil.copyfileobj(sys.stdin.buffer,stream); stream.close()' < backups/collabpad-YYYY-MM-DD.db
   ~~~

   Команда отказывается перезаписывать существующую базу. Спрайты из `data/sprites/` так не
   переносятся.

3. `docker compose up -d --wait`

## Без Docker

Нужны Python 3.12 и Node.js 20.19+ (на Ubuntu при необходимости — `python3.12-venv`).
Подготовка с чистого клона:

~~~bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd web && npm ci && npm run build && cd ..
~~~

Если `ensurepip` отсутствует, но системный pip есть:

~~~bash
python3.12 -m venv --without-pip .venv
python3.12 -m pip --python .venv install -r requirements.txt
~~~

Запуск:

~~~bash
./start.sh
~~~

Открой http://127.0.0.1:8000/w (API: http://127.0.0.1:8000/docs). `start.sh` слушает только
локальный адрес, собирает фронтенд, только если нет `app/static`, и создаёт `.env` из
`.env.example`, если его нет. Данные — в `data/` (база `data/collabpad.db`, спрайты
`data/sprites/`); они переживают перезапуск и пересборку.

Для разработки с горячей перезагрузкой — сервер:

~~~bash
env -u PYTHONPATH .venv/bin/python -m uvicorn app.main:asgi --reload --port 8000 --no-access-log
~~~

и во втором терминале Vite:

~~~bash
cd web && npm run dev
~~~

Открой http://localhost:5173. Локальные origin перечислены в `.env.example`; если меняешь
`PORT` или адрес Vite, поправь `CORS_ORIGINS`.

Без ключа нейросети оживление работает через ручной выбор типа; без SMTP письма (коды входа,
приглашения) пишутся в лог сервера; без тестовых паролей Robokassa оплата отвечает
`PAYMENT_FAILED`.

## Проверки

~~~bash
env -u PYTHONPATH .venv/bin/python -m pytest -q
~~~

~~~bash
cd web && npm test && npm run build
~~~

Перед `pytest` останови запущенный dev-сервер: два чувствительных ко времени теста не любят
нагрузку. Сценарии из §12 в двух браузерах — `npm run e2e` в `web/` (нужен установленный
Google Chrome; сам собирает фронтенд и поднимает сервер на порту 8030 с временной базой).
Точность нейросети — `scripts/vlm_accuracy.py` (тратит деньги, около $0.25 за прогон; отчёт —
[`reports/vlm-accuracy.md`](reports/vlm-accuracy.md)). Нагрузка — `scripts/loadtest.py`
(отчёт — [`reports/loadtest.md`](reports/loadtest.md)).

Те же `pytest`, `npm test`, `npm run build` и `npm run e2e` запускает GitHub Actions перед каждой выкладкой
(push в ветку `deploy`, см. [`deploy.md`](deploy.md), шаг 11).

Клиентские тесты используют встроенный `node --test`. Ruff — только внешний форматтер.

## Generated animation

The optional description next to ALIVE invokes choreography v1 (spec §4a). It uses the
same `VLM_API_KEY`, `VLM_PROXY_URL` and daily budget as classification;
`config/vlm.yaml` sets generation output tokens and performance limits.
`parts.md` v4 identifies geometry from the image alone; after preparing the actual
layers/pivots, `generate.md` v3 composes motion from text and fixed geometry only.
`motion_model` / `motion_fallback_model` optionally select a separate text LLM;
otherwise the vision models are used. Both stages share one job deadline and budget.
Part identification uses `drawing_parts_v2` / `version:2` with explicit
`kind:part|wheel`. Generated wheels use whole-stroke elliptical ownership so a body
line crossing the bounding box is not cut into the rotating layer. Chunk fragments
are joined for this check and coverage uses arc length, not sample counts. Other
parts retain their existing splitting. Existing saved atlases are not rebuilt;
verify this change on a newly generated drawing. Motion and canonical plans stay v1.
Coordinate relationships are checked within the provider retry/fallback loop, so
an invalid box or pivot can recover inside one ALIVE request. These attempts use
the existing deadline, retry count and daily budget. Geometry errors name the part
and offending field; only `1e-9` normalized floating-point roundoff is tolerated.
`generation.hard_timeout_s: 90` covers the complete generated job, with
`generation.request_timeout_s: 30` per HTTP attempt. Ordinary ALIVE still uses 25 s
and 10 s respectively. HTTP 400/nontransient 4xx advance directly to the fallback;
408, 429 and server errors retain the configured retry policy. Generation requests
require endpoints supporting structured output through `provider.require_parameters`.
Gemini motion requests use a compact schema without array `maxItems` bounds to
avoid its "constraint ... too many states for serving" rejection. The full server
model still checks track/key/repeat counts; Qwen and other providers receive the
complete schema. The compact schema affects the provider's decoder only.
The author sees a collapsible Drawing parts panel with boxes and pivot crosses;
it persists through playback or a later failure until another ALIVE or page reload.
`alive_job.vlm_result` records `identified`, `prepared`, `motion` and prompt versions
for diagnostics. Strokes and consumed/fossil rows retain their existing semantics.
The file must include the `generation` section (including `description_chars: 1000`);
both ALIVE paths validate against it before calling the provider. After changing this
file in a local Docker setup, rebuild the app with `docker compose up --build -d --wait`:
the config is copied into the image, so a container restart alone does not refresh it.
`generation.loop_pause_ms: 2000` holds the final pose for two seconds before the complete
validated performance repeats from its fixed origin. This also applies to previously
completed visual entities and requires no additional model calls. No new dependency
or database migration is required. With no key, descriptions fail with a localized error;
blank descriptions still offer the manual type grid.

`tests/test_choreography.py`, `tests/test_generated_alive.py` and client tests cover the
compiler, atlas, provider failures, isolation, sleep/freeze/recovery and shared Python/JS
interpolation. Playwright uses `tests.browser_server` with a deterministic provider for
text animations and an unavailable classifier for the ordinary chooser scenarios. There
is no production fake-provider setting. `web/e2e/generated.spec.js` exercises two viewers,
late join, reload, repeated performances with a two-second pause and editable failures in RU/EN. Set `E2E_CHROMIUM` to an
existing Chromium executable if Google Chrome is unavailable. Real-provider quality is
a separate budgeted check; the fake provider makes no network calls.

### Diagnosing a failed animation

Expected ALIVE failures now produce a WARNING even with the Docker image's default
log level. It includes the job ID, stage, error code and the concrete failure reason,
for example `stage=prepare code=ANIMATION_INVALID reason=part=head: ...`.
`vlm_result.failure` retains that information with the completed provider/preparation
results. A missing Drawing parts panel means the failure happened before its event:
image identification, refusal or layer preparation. Contract/HTTP failures also log
invalid field paths or upstream HTTP status and message (including OpenRouter's
nested provider error in `error.metadata.raw`), without request images,
action text, schema input values or API keys.

After a local container rebuild, reproduce once and inspect `docker compose logs
--tail=100 app`. No need to raise the application's log level or turn on HTTP debug
logging. Read old job results using the application's configured path (not a guessed
volume/file name); this also works after `docker compose down` if the data volume is
still present:

~~~bash
docker compose run --rm --no-deps -T --entrypoint python app - <<'PY'
from pathlib import Path
from app.config import Settings
import sqlite3
settings = Settings()
path = Path(settings.db_path).resolve()
print('Database:', path)
print('Files:', [p.name for p in path.parent.iterdir()] if path.parent.is_dir() else 'directory missing')
if path.is_file():
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    for row in connection.execute(
        'SELECT id,error_code,timings,vlm_result FROM alive_job '
        'WHERE status=? ORDER BY created_at DESC LIMIT 3', ('failed',)
    ):
        print(dict(row))
PY
~~~

A missing file means this Compose invocation is not reading the expected database;
check the Compose project/data-volume selection. This read-only query never creates
a new SQLite database. Previously swallowed preparation reasons cannot be recovered
from old logs; the saved `identified` geometry can still help reproduce them.
