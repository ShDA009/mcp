# zephyr-mcp — контекст для разработки

Общее описание проекта, установка, список tools — в [README.md](README.md).
Здесь только то, что не очевидно из кода: специфика стороннего API и решения,
которые не стоит переоткрывать заново.

## Специфика ATM (Adaptavist Test Management), не Zephyr Squad

Self-hosted Jira Server/DC. Плагин — **Zephyr Scale (ATM)**, не Zephyr Squad —
это важно, т.к. большая часть публичной документации/примеров в сети про
Zephyr Squad (`/rest/zephyr/1.0`, Basic Auth email+token), что здесь не
работает. Аутентификация — **Bearer PAT** (`Authorization: Bearer {token}`),
email нигде не используется.

Базовые пути:
- ATM (тест-кейсы, циклы, executions): `{ZEPHYR_BASE_URL}/rest/atm/1.0/`
- Jira REST (только `get_project`/`list_projects` — ATM `/project` даёт 500):
  `{ZEPHYR_BASE_URL}/rest/api/2/`

Оба пути используют один и тот же Bearer-токен.

**Перед реализацией любого нового тула — сначала пробный curl на реальном
инстансе** (project `CLOUDDEV`, ключи вида `CLOUDDEV-T853`/`CLOUDDEV-C667`), а
не общая документация ATM — версии плагина расходятся в деталях путей и кодах
ошибок (например 500 вместо 404 на неподдерживаемый путь).

## Грабли API (подтверждено curl на реальном инстансе)

- **Test steps и results нет отдельным эндпоинтом.** `/testcase/{key}/teststeps`
  и `/testcase/{key}/testscript` — оба 404. Шаги лежат внутри
  `testcase.testScript.steps` (обычный `GET /testcase/{key}`).
- **Execution-детали — `/testrun/{key}/testresults` (множественное число),**
  не `/testrun/{k}/testcase/{k}/testresult` (это write-путь: `OPTIONS`
  подтвердил `Allow: OPTIONS,POST,PUT`, GET даёт 500 с пустым телом).
- **`folder =` в JQL матчит только точные листовые пути**, реально
  присутствующие в индексе. Промежуточные узлы дерева без своих ТК (например
  `/Турбо`, где 663 ТК лежат в подпапках) дают `[]` при точном совпадении и
  `400` при попытке `~` (contains) — сервер не поддерживает
  префиксный/contains-поиск по `folder` вообще.
- **`folder`-фильтр в `list_test_cases`/`list_cycles` поэтому реализован
  client-side**, не через JQL: сервер делает один запрос с
  `fields=key,folder,name` (sparse fieldset — полные объекты для всего проекта
  CLOUDDEV дают ~54с, sparse — ~9с) и `maxResults=5000`, затем фильтрует по
  `folder == prefix or folder.startswith(prefix + "/")` на клиенте
  (`_filter_by_folder_prefix`). Возвращает только лёгкую проекцию
  (`key`/`folder`/`name`), не полные объекты.
- **Батчинг по `KEY_IN_BATCH_SIZE=100` обязателен** для `get_test_cases_batch`/
  `get_cycles_batch`: один запрос `key IN (...)` со всеми 663 ключами `/Турбо`
  дал `414 URI Too Long`. Замер: 50 ключей → 1.4с, 100 → 2.3с, 150 → 3.3с,
  200 → 4.1с (линейный рост ~20мс/ключ) — 100 выбран с запасом до
  неизвестной точной границы (она где-то между 200 и 663).

## Запуск через uvx (без Docker)

Entry point `zephyr-mcp` (`[project.scripts]`) запускается через
`uvx --from git+<repo>#subdirectory=zephyr-mcp zephyr-mcp`. Установочные
скрипты в `install/` — зеркало `outlook-mcp/install/`: находят/ставят `uv`,
пишут `.env` (chmod 600, `~/.config/zephyr-mcp` на macOS/Linux,
`%USERPROFILE%\.zephyr-mcp\.env` на Windows), идемпотентно обновляют секцию
`zephyr-scale` в `cline_mcp_settings.json` — **без кредов**, только
`command`/`args`/`disabled`/`transportType`.

**`main()` в `__main__.py` перехватывает `--help`/`-h` ДО `load_config()`** —
иначе `SystemExit` об отсутствии env не дал бы вывести справку; это лёгкая
самопроверка для install-скриптов.

**`config.py` сам читает `.env`** как fallback, если переменной нет в
`os.environ` (путь выбирается через `platform.system()`, должен совпадать с
тем, что пишет install-скрипт) — так секреты не дублируются в JSON-конфиге
Cline. `os.environ` приоритетнее файла. Тесты изолируются от реального `.env`
на машине разработчика через autouse-фикстуру `no_env_file` в
`tests/test_config.py`.

## Обязательные требования к новой логике

- Таймаут HTTP-запросов 15s; 401/403/404/429 разбираются в читаемые ошибки
  (429 — retry с backoff, ≤3 попытки, честный `Retry-After` либо экспонента);
  невалидный JSON в ответе не должен падать необработанным исключением.
- Не логировать `ZEPHYR_API_TOKEN` ни в каком виде (в т.ч. не как обычное
  repr-поле датакласса).
- Юнит-тесты в `tests/` на чистые функции без сети (`_filter_by_folder_prefix`,
  `_handle_response`, `_retry_delay`, `_escape_jql`, `_chunk`,
  `config.load_config`). Запуск: `uv run pytest tests/ -v`.
- Конфигурация должна фейлиться быстро (fail fast) при старте, если не заданы
  обязательные env-переменные, с понятным сообщением какой не хватает. Лог —
  только в stderr (stdout занят JSON-RPC).
