# zephyr-mcp

MCP-сервер (stdio) для Zephyr Scale (Adaptavist Test Management, ATM) на self-hosted Jira Server/DC.

## Сборка

```
docker build -t zephyr-mcp:latest .
```

## Подключение через Cline (VS Code)

VS Code → Cline → MCP Servers → Configure → `cline_mcp_settings.json`:

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

## Подключение через OpenCode

`opencode.jsonc`:

```json
{
  "mcp": {
    "zephyr-scale": {
      "type": "local",
      "command": [
        "docker", "run", "-i", "--rm",
        "--env-file", "${MCP_DIR}/zephyr-mcp/.env",
        "zephyr-mcp:latest"
      ],
      "enabled": true
    }
  }
}
```

## Переменные окружения

Создайте файл `.env` в директории `zephyr-mcp/`:

```
ZEPHYR_BASE_URL=https://tasks.example.com
ZEPHYR_API_TOKEN=your_token_here
```

- `ZEPHYR_BASE_URL` — URL Jira, например `https://tasks.example.com`
- `ZEPHYR_API_TOKEN` — Bearer-токен (Personal Access Token)

## Локальный запуск (без Docker)

```
uv sync
ZEPHYR_BASE_URL=https://tasks.example.com \
ZEPHYR_API_TOKEN=... \
uv run zephyr-mcp
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

## Тесты

```
uv sync
uv run pytest tests/ -v
```
