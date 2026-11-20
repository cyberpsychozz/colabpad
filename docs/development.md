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

- `Dockerfile` собирает интерфейс в стадии Node.js (и прогоняет клиентские тесты),
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
приглашения) пишутся в лог сервера; без тестовых ключей ЮKassa оплата отвечает
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
