# zephyr-mcp

MCP-сервер (stdio) для Zephyr Scale (Adaptavist Test Management, ATM) на self-hosted Jira Server/DC.

Запускается через `uvx` (рекомендуется для сотрудников) или в Docker.

## Установка для сотрудников (uvx, без Docker)

Готовые установочные скрипты в [install/](install/) — под Windows, macOS
(Apple Silicon) и Linux. Скрипт находит/ставит `uv`, спрашивает креды,
сохраняет `.env` и прописывает сервер в Cline идемпотентно. Инструкция —
[install/README.md](install/README.md).

Запуск сервера под капотом:

```bash
uvx --from git+https://github.com/ShDA009/mcp.git#subdirectory=zephyr-mcp zephyr-mcp
```

`zephyr-mcp` — консольный entry point (см. `[project.scripts]` в
[pyproject.toml](pyproject.toml)). `--help` печатает справку и завершается без
запуска stdio-сессии (используется скриптами для проверки установки).

## Сборка и запуск (Docker, альтернатива)

При установке через `install/` (раздел выше) весь этот раздел не нужен —
скрипт сам находит/ставит `uv`, пишет `.env` в `~/.config/zephyr-mcp/.env` и
прописывает Cline. Ниже — только для тех, кто предпочитает Docker вместо uvx.

```bash
docker build -t zephyr-mcp:latest .
```

Переменные окружения — создать файл `.env` в директории `zephyr-mcp/`:

```
ZEPHYR_BASE_URL=https://tasks.example.com
ZEPHYR_API_TOKEN=your_token_here
```

- `ZEPHYR_BASE_URL` — URL Jira, например `https://tasks.example.com`
- `ZEPHYR_API_TOKEN` — Bearer-токен (Personal Access Token)
- `ZEPHYR_ALLOW_WRITE` — необязательная, **по умолчанию выключена**.
  Включается значением `1`/`true`/`yes`/`on`. Пока выключена, пишущие tools
  не регистрируются: клиент их не видит и вызвать не может. Zephyr общий для
  команды, поэтому запись включается осознанно, в отличие от `outlook-mcp`,
  где `EWS_ALLOW_WRITE` включён по умолчанию.
- `ZEPHYR_ALLOW_DELETE` — необязательная, **по умолчанию выключена**.
  Регистрирует `delete_test_runs` и действует только вместе с
  `ZEPHYR_ALLOW_WRITE`. Удаление необратимо, поэтому право на запись его не
  включает. Tool помечен `destructiveHint`, клиенты с поддержкой этой
  аннотации спрашивают подтверждение перед вызовом.

Фрагмент `cline_mcp_settings.json` для Docker-варианта:

```json
{
  "mcpServers": {
    "zephyr-scale": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "--env-file", "${MCP_DIR}/zephyr-mcp/.env",
        "zephyr-mcp:latest"
      ],
      "disabled": false
    }
  }
}
```

## Тулы

| Тул | Параметры | Описание |
|---|---|---|
| `list_executions` | `test_run_key` | Список test executions (items) внутри test run/cycle, например `PROJ-C667` |
| `get_execution` | `test_run_key`, `test_case_key=None` | Детальный результат execution(ов) со статусами по шагам; без `test_case_key` — все executions в run |
| `get_test_case` | `test_case_key` | Test case по ключу, например `PROJ-T853` (шаги — в `testScript.steps`) |
| `list_cycles` | `project_key`, `folder=None`, `max_results=50` | Поиск test runs (cycles) в проекте, например `PROJ`; с `folder` — только внутри папки и её подпапок (префикс пути, например `/Папка`). С `folder` возвращает лёгкий список (`key`/`folder`/`name`), не полные объекты — используй `get_cycles_batch` для деталей |
| `list_test_cases` | `project_key`, `folder=None`, `max_results=50` | Поиск test cases в проекте; с `folder` — только внутри папки и её подпапок (префикс пути, например `/Папка` или `/Папка/Подпапка`). С `folder` возвращает лёгкий список, не полные объекты — используй `get_test_cases_batch` для деталей |
| `get_test_cases_batch` | `project_key`, `test_case_keys` | Полные test case объекты (с шагами) по списку ключей |
| `get_cycles_batch` | `project_key`, `test_run_keys` | Полные test run объекты (с executions) по списку ключей |
| `get_project` | `project_id_or_key` | Jira-проект по числовому id или ключу — резолвит id из URL (например `16816`) в `project_key` (например `PROJ`) |
| `list_projects` | — | Список всех доступных токену Jira-проектов (id, key, name) |
| `create_test_case` | `project_key`, `name`, `steps`, `objective=None`, `precondition=None`, `folder=None`, `labels=None` | Создать тест-кейс. Доступен только при `ZEPHYR_ALLOW_WRITE=1`. `steps` — список словарей (`description` обязателен, `expectedResult`/`testData` опциональны). Текстовые поля сохраняются как HTML. `folder` — путь существующей папки, папка не создаётся |
| `update_test_case` | `test_case_key`, `name=None`, `objective=None`, `precondition=None`, `folder=None`, `labels=None`, `steps=None`, `status=None` | Обновить тест-кейс по ключу. Доступен только при `ZEPHYR_ALLOW_WRITE=1`. Обновляются только переданные поля, остальные не трогаются. `steps` при передаче заменяет весь список шагов, а не дополняет его. `status` — статус ТК (например `Draft`, `Approved`, `Deprecated`), невалидное значение сервер отвергнет |
| `create_folder` | `project_key`, `path`, `folder_type='TEST_CASE'` | Создать папку. Доступен только при `ZEPHYR_ALLOW_WRITE=1`. `path` — полный путь от корня проекта с ведущим слэшем, например `/Папка/Подпапка`. Возвращает числовой id папки |
| `create_test_run` | `project_key`, `name`, `test_case_keys=None`, `folder=None` | Создать тест-цикл. Доступен только при `ZEPHYR_ALLOW_WRITE=1`. `test_case_keys` — опциональный список ключей кейсов для предварительного заполнения, можно создать пустой цикл. `folder` — путь существующей папки, папка не создаётся |
| `add_test_cases_to_run` | `test_run_key`, `test_case_keys` | Добавить тест-кейсы в существующий цикл. Доступен только при `ZEPHYR_ALLOW_WRITE=1`. Дубликаты уже присутствующих ключей молча пропускаются; реализация шлёт `POST /testrun/{key}/testresults` с массивом только новых ключей |
| `add_execution_result` | `test_run_key`, `test_case_key`, `status`, `comment=None`, `step_statuses=None` | Проставить результат execution для кейса, уже входящего в цикл. Доступен только при `ZEPHYR_ALLOW_WRITE=1`. Обновляет существующую execution кейса на месте, без дублей. Кейс должен быть уже добавлен в цикл (`create_test_run`/`add_test_cases_to_run`), иначе ошибка. `step_statuses` — статусы по шагам по порядку (только статус, без текста фактического результата) |
| `archive_test_cases` | `test_case_keys` | Заархивировать тест-кейсы по ключам. Доступен только при `ZEPHYR_ALLOW_WRITE=1`. Кейсы получают `archived: true` и скрываются из обычных списков. Ключи резолвятся в числовые id через `/rest/tests/1.0/testcase/{key}?fields=id`; запрос — `POST /rest/tests/1.0/testcase/bulk/archive` с массивом id. Дубли ключей схлопываются |
| `unarchive_test_cases` | `test_case_keys` | Восстановить заархивированные тест-кейсы по ключам. Доступен только при `ZEPHYR_ALLOW_WRITE=1`. Обратная операция к `archive_test_cases`; запрос — `POST /rest/tests/1.0/testcase/bulk/unarchive` с массивом числовых id |
| `delete_test_runs` | `test_run_keys` | Удалить тест-циклы по ключам, **необратимо**. Доступен только при `ZEPHYR_ALLOW_WRITE=1` и `ZEPHYR_ALLOW_DELETE=1`. Ключи резолвятся в числовые id через `/rest/tests/1.0/testrun/{key}?fields=id`; запрос — `POST /rest/tests/1.0/testrun/bulk/delete` с массивом id. Удалённые циклы возвращают 404 |

---

Разделы ниже — для разработки и отладки сервера, не нужны для установки и
обычного использования.

## Локальный запуск для разработки (без Docker и без uvx)

Для отладки прямо из клонированного репозитория, без публикации/git-URL:

```bash
uv sync
ZEPHYR_BASE_URL=https://tasks.example.com \
ZEPHYR_API_TOKEN=... \
uv run zephyr-mcp
```

## Тесты

```
uv sync
uv run pytest tests/ -v
```
