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
| `list_executions` | `test_run_key` | Список test executions (items) внутри test run/cycle, например `CLOUDDEV-C667` |
| `get_execution` | `test_run_key`, `test_case_key=None` | Детальный результат execution(ов) со статусами по шагам; без `test_case_key` — все executions в run |
| `get_test_case` | `test_case_key` | Test case по ключу, например `CLOUDDEV-T853` (шаги — в `testScript.steps`) |
| `list_cycles` | `project_key`, `folder=None`, `max_results=50` | Поиск test runs (cycles) в проекте, например `CLOUDDEV`; с `folder` — только внутри папки и её подпапок (префикс пути, например `/Турбо`). С `folder` возвращает лёгкий список (`key`/`folder`/`name`), не полные объекты — используй `get_cycles_batch` для деталей |
| `list_test_cases` | `project_key`, `folder=None`, `max_results=50` | Поиск test cases в проекте; с `folder` — только внутри папки и её подпапок (префикс пути, например `/Турбо` или `/Турбо/Портал`). С `folder` возвращает лёгкий список, не полные объекты — используй `get_test_cases_batch` для деталей |
| `get_test_cases_batch` | `project_key`, `test_case_keys` | Полные test case объекты (с шагами) по списку ключей |
| `get_cycles_batch` | `project_key`, `test_run_keys` | Полные test run объекты (с executions) по списку ключей |
| `get_project` | `project_id_or_key` | Jira-проект по числовому id или ключу — резолвит id из URL (например `16816`) в `project_key` (например `CLOUDDEV`) |
| `list_projects` | — | Список всех доступных токену Jira-проектов (id, key, name) |

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
