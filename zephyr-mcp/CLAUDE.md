# MCP-сервер для Zephyr Scale / ATM (Jira Server/DC)

## Контекст

Self-hosted Jira Server/DC (версия 8.22.6). Плагин — **Zephyr Scale
(Adaptavist Test Management, ATM)**, не Zephyr Squad. Подтверждено серией диагностических curl-запросов
к реальному инстансу (см. "Подтверждённое API" ниже) — исходное предположение о Zephyr Squad
(`/rest/zephyr/1.0`, Basic Auth email+token) оказалось неверным.

Готовых MCP-серверов под эту связку (self-hosted ATM + Bearer PAT) не найдено — пишем с нуля.

## Цель

MCP-сервер (stdio transport) для чтения и работы с test cases, test runs (cycles), test executions
через ATM REST API, чтобы делать ревью тест-кейсов и обновлять статусы execution прямо из диалога.

## Технологии

- Python (однороден с уже работающим MCP-сервером `mcp-atlassian` в том же окружении)
- MCP SDK: official `mcp` (PyPI) — `from mcp.server.fastmcp import FastMCP`
- HTTP-клиент: `httpx`
- Менеджер зависимостей: `uv`
- Конфигурация: переменные окружения (без хардкода секретов)
- Деплой: Docker (`python:3.12-slim`), stdio-транспорт, подключение через **Cline** (VS Code),
  конфиг в `cline_mcp_settings.json` — не Claude Desktop/Code

## Аутентификация

**Bearer PAT**, не Basic Auth. Заголовок `Authorization: Bearer {token}` на каждый запрос.
Email не требуется и нигде не используется.

Переменные окружения:
- `ZEPHYR_BASE_URL` — например `https://jira.example.com`
- `ZEPHYR_API_TOKEN` — Personal Access Token

## Базовый путь API

- ATM (тест-кейсы, циклы, executions): `{ZEPHYR_BASE_URL}/rest/atm/1.0/`
- Jira REST (проекты — `get_project`/`list_projects`, т.к. ATM `/project` даёт 500): `{ZEPHYR_BASE_URL}/rest/api/2/`

Оба пути используют один и тот же Bearer-токен.

## Подтверждённое API (проверено curl на реальном инстансе, проект `CLOUDDEV`)

| Запрос | Метод + путь | Статус | Примечание |
|---|---|---|---|
| Получить test case | `GET /testcase/{key}` | 200 | Полный объект, шаги — в `testScript.steps`, отдельного `/teststeps` эндпоинта нет (404) |
| Поиск test cases по проекту | `GET /testcase/search?query={JQL}&maxResults=` | 200 | JQL вида `projectKey = "CLOUDDEV"` |
| Поиск test runs (cycles) по проекту | `GET /testrun/search?query={JQL}&maxResults=` | 200 | Возвращает test run'ы с `key` (`CLOUDDEV-C667`) и вложенным `items[]` |
| Получить test run | `GET /testrun/{key}` | 200 | Даёт `items[]` — это и есть executions: `{id, testCaseKey, status, assignedTo, executedBy}` |
| Test steps отдельным путём | `GET /testcase/{key}/teststeps` | 404 | Не существует — шаги внутри `testcase` |
| Test results на уровне test case | `GET /testcase/{key}/testresults` | 404 | Не тот путь |
| Test result по testRun+testCase | `GET /testrun/{runKey}/testcase/{caseKey}/testresult` | 500 (пустое тело) | GET не поддерживается — `OPTIONS` на этот путь подтвердил `Allow: OPTIONS,POST,PUT`, значит это write-путь (см. `update_execution_status`) |
| Test result по одной id | `GET /testresult/{id}` | 404 | Не тот путь |
| Test results (все) для test run | `GET /testrun/{runKey}/testresults` | 200 | **Правильный путь для execution-деталей** — множественное число, без `/testcase/{key}/`. Отдаёт массив с `testCaseKey`, `automated`, `scriptResults[]` (step-level статусы и описания) |
| Test steps отдельным путём (2) | `GET /testcase/{key}/testscript` | 404 | Тоже не существует — подтверждает, что шаги только внутри `testcase.testScript.steps` |
| Test directories (folder tree) | `GET /folder/tree/testcase/{projectKey}` | 404 | Не тот путь |
| Test directories (folder list) | `GET /folder?projectKey={key}` | 500 (пустое тело) | Путь матчится роутингом (не 404), но GET без доп. параметров не работает — не разведано, чего не хватает |
| Test cases по папке (лист) | `GET /testcase/search?query=projectKey = "X" AND folder = "/путь/к/листовой/папке"` | 200 | `folder =` работает **только для точных листовых путей**, которые реально хранятся в индексе (например `/6. Разовые задачи` без вложенных подпапок). Для промежуточных узлов дерева (есть подпапки, но нет ТК прямо в узле) возвращает `[]` — см. ниже про `/Турбо` |
| Test cases по папке (промежуточный узел) | `GET /testcase/search?query=... AND folder = "/Турбо"` | 200, но `[]` | `/Турбо` — контейнер с 663 ТК только в подпапках (`/Турбо/Портал/...`, `/Турбо/Сайт/...`), у самого узла нет ТК → нет записи в индексе `folder`. `folder ~ "Турбо"` даёт `400 {"errorMessages":["Value(s) not found for field folder: Турбо"]}` — сервер не поддерживает префиксный/contains-поиск вообще, только точное совпадение существующих листовых значений |
| Test runs по папке | `GET /testrun/search?query=projectKey = "X" AND folder = "/путь/к/папке"` | 200 | Тот же принцип — точное совпадение листа, не префикс |
| ATM список проектов | `GET /rest/atm/1.0/project` | 500 (пустое тело) | Не работает — используем стандартный Jira REST вместо ATM |
| Jira проект по id | `GET /rest/api/2/project/{id}` | 200 | Резолвит числовой `projectId` (например из URL) в объект с полем `key` (например `16816` → `CLOUDDEV`) |
| Jira список проектов | `GET /rest/api/2/project` | 200 | Полный список (~180 проектов на инстансе), поля `id`, `key`, `name` — используем только их, остальное (`versions`, `components`, `issueTypes` для одиночного проекта) не нужно |

**list_executions реализован как:** `GET /testrun/{test_run_key}` → вернуть `items[]`. Это означает,
что executions читаются по ключу test run/cycle (`CLOUDDEV-C667`), а не по Jira issue id, как
предполагалось изначально.

**get_execution реализован как:** `GET /testrun/{test_run_key}/testresults` → вернуть весь массив,
либо отфильтровать на стороне клиента по `testCaseKey`, если передан `test_case_key` (отдельного
эндпоинта на одно execution нет — `/testresult/{id}` даёт 404, `/testrun/{k}/testcase/{k}/testresult` даёt 500 на GET).

**`folder`-фильтр в `list_test_cases`/`list_cycles` реализован client-side, не через JQL, и возвращает
только лёгкую проекцию (`key`/`folder`/`name`), не полные объекты.** Причины и цепочка находок:

1. ATM `folder =` матчит только точные листовые пути, реально присутствующие в индексе — узлы дерева
   без собственных ТК/циклов (например `/Турбо`, у которого 663 ТК лежат в подпапках `/Турбо/Портал/...`,
   `/Турбо/Сайт/...`) дают `[]` при точном совпадении и `400` при попытке `~` (contains). Сервер вообще
   не поддерживает префиксный/contains-поиск по `folder`.
2. Значит единственный способ получить "все ТК под `/Турбо`" — просмотреть весь проект и отфильтровать
   на клиенте. Но полные объекты (с `testScript`/HTML-описаниями) для всего проекта CLOUDDEV (2428 ТК)
   отдаются **~54 секунды** — не укладывается ни в какой разумный HTTP-таймаут.
3. Sparse fieldset **работает**: `fields=key,folder,name` даёт тот же ответ за **~9 секунд** — тормозит
   именно серверный поиск/сериализация, а не объём тела, но для лёгкой проекции это уже приемлемо.
4. Соответственно `list_test_cases`/`list_cycles` с `folder` делают ОДИН запрос с `fields=key,folder,name`
   и `maxResults=5000`, затем фильтруют по `folder == prefix or folder.startswith(prefix + "/")` на клиенте
   (`_filter_by_folder_prefix`) — и возвращают именно эту лёгкую проекцию, **не полные объекты**.
5. Получить полные объекты для конкретных ключей — отдельные тулы `get_test_cases_batch`/`get_cycles_batch`,
   которые строят `key IN (...)` и батчат ключи по `KEY_IN_BATCH_SIZE=100`. Батчинг обязателен: один запрос
   `key IN (...)` со всеми 663 ключами `/Турбо` дал `414 URI Too Long` (подтверждено на реальном инстансе).
   Замер безопасного размера батча на том же инстансе: 50 ключей → 1.4с, 100 → 2.3с, 150 → 3.3с, 200 → 4.1с
   (все `200 OK`, линейный рост ~20мс/ключ) — 100 выбран с запасом до неизвестной точной границы 414
   (она где-то между 200 и 663).

## Ресурсы и MCP-тулы

| MCP-тул | Статус | HTTP | Endpoint | Примечание |
|---|---|---|---|---|
| `list_executions` | ✅ реализован, проверен | GET | `/testrun/{test_run_key}` → `items[]` | Milestone 1, подтверждён end-to-end через Cline на `CLOUDDEV-C667` |
| `get_execution` | ✅ реализован | GET | `/testrun/{test_run_key}/testresults` (+ фильтр по `testCaseKey` на клиенте) | Путь подтверждён curl, тул зарегистрирован — end-to-end через Cline ещё не проверен |
| `get_test_case` | ✅ реализован | GET | `/testcase/{key}` | Путь подтверждён curl, тул зарегистрирован — end-to-end через Cline ещё не проверен |
| `list_cycles` | ✅ реализован | GET | `/testrun/search?query={JQL}&maxResults=` | `folder` — префикс поддерева, матчится **на клиенте**; с `folder` возвращает лёгкую проекцию (`key`/`folder`/`name`), не полные объекты — см. ниже почему |
| `list_test_cases` | ✅ реализован | GET | `/testcase/search?query={JQL}&maxResults=` | То же самое; закрывает потребность "все ТК в папке" без отдельного `list_directories` |
| `get_test_cases_batch` | ✅ реализован | GET | `/testcase/search?query=...key IN (...)` | Полные объекты по списку ключей (например из `list_test_cases(folder=...)`), батчами по `KEY_IN_BATCH_SIZE=100` |
| `get_cycles_batch` | ✅ реализован | GET | `/testrun/search?query=...key IN (...)` | Полные объекты (с `items[]`) по списку ключей циклов, тот же батчинг |
| `get_project` | ✅ реализован | GET | `/rest/api/2/project/{id_or_key}` | Резолвит `projectId` (например из URL) в `project_key`; вне таблицы исходного плана, добавлен по запросу пользователя |
| `list_projects` | ✅ реализован | GET | `/rest/api/2/project` | Список проектов, сжат до `id`/`key`/`name`; вне таблицы исходного плана, добавлен по запросу пользователя |
| `create_cycle` | не реализован | POST | `/testrun` | Путь **не проверен** |
| `update_execution_status` | не реализован | PUT (метод подтверждён через OPTIONS) | `/testrun/{runKey}/testcase/{caseKey}/testresult` | Метод известен (`Allow: OPTIONS,POST,PUT`), но тело запроса не разведано — нужен пробный PUT с догадкой о JSON-теле (например `{"status": "Pass"}`). Отложено — пока нужно только чтение прогонов, не запись |
| `create_execution` | не реализован | ? | ? | Не разведано |
| `get_test_steps` | не реализован | — | входит в `GET /testcase/{key}` → `testScript.steps` | Отдельного эндпоинта нет (проверены `/teststeps` и `/testscript` — оба 404). Отдельный тул, вероятно, не нужен — данные уже есть в `get_test_case` |
| `add_test_step` | не реализован | ? | ? | Не разведано |
| `list_directories` | **отменён** | — | — | Не нужен отдельным тулом — задача "список ТК/циклов по папке" закрыта через `folder`-фильтр в `list_test_cases`/`list_cycles` |

Примечание: перед реализацией каждого нового тула — сначала пробный curl на реальном инстансе
(project `CLOUDDEV`, реальные ключи вроде `CLOUDDEV-T853`, `CLOUDDEV-C667`), а не полагаться на
общую документацию ATM — версии плагина отличаются в деталях путей и кодов ошибок (например 500
вместо 404 на некорректный/неподдерживаемый путь).

## Обязательные требования к реализации

1. **Обработка ошибок по умолчанию** (без напоминаний):
   - Таймаут HTTP-запросов (15s), явная ошибка при истечении.
   - Разбор кодов ответа: 401/403 — "неверные учётные данные или нет доступа к проекту",
     404 — "сущность не найдена", 429 — retry с backoff (≤3 попытки, честный `Retry-After` либо экспонента).
   - Невалидный JSON в ответе — не падать необработанным исключением, вернуть читаемую ошибку тулу.
   - Не логировать `ZEPHYR_API_TOKEN` ни в каком виде (в т.ч. не хранить как обычное repr-поле датакласса).

2. **Тесты обязательны для новой логики.** Юнит-тесты в `tests/` (pytest, dev-зависимость в
   `pyproject.toml`) — на чистые функции без сети (`_filter_by_folder_prefix`, `_handle_response`,
   `_retry_delay`, `_escape_jql`, `_chunk`, `config.load_config`). Запуск: `uv run pytest tests/ -v`.

3. Конфигурация должна фейлиться быстро (fail fast) при старте, если не заданы обязательные env-переменные,
   с понятным сообщением какой переменной не хватает. Лог — только в stderr (stdout занят JSON-RPC).

4. Структура пакета:
   ```
   pyproject.toml / uv.lock
   src/zephyr_mcp/
     __init__.py
     __main__.py   — точка входа: собрать сервер, mcp.run()
     config.py     — парсинг env, fail-fast
     client.py     — HTTP-клиент (httpx), Bearer auth, таксономия ошибок, 429 backoff
     tools.py      — регистрация MCP-тулов
   tests/          — pytest, покрывает чистую логику client.py и config.py
   Dockerfile
   README.md
   ```

6. **Запуск через uvx (без Docker).** entry point `zephyr-mcp`
   (`[project.scripts]`) запускается через
   `uvx --from git+<repo>#subdirectory=zephyr-mcp zephyr-mcp`. Папка `install/`
   содержит установочные скрипты для сотрудников (`setup.sh` macOS arm64 +
   Linux, `setup.ps1` Windows) — зеркало `outlook-mcp/install/`: находят/ставят
   `uv`, пишут `.env` (chmod 600, каталог `~/.config/zephyr-mcp` на macOS/Linux,
   `%USERPROFILE%\.zephyr-mcp\.env` на Windows), идемпотентно обновляют секцию
   `zephyr-scale` в `cline_mcp_settings.json` — **без кредов**, только
   `command`/`args`/`disabled`/`transportType`. Спрашивают 2 переменные
   (`ZEPHYR_BASE_URL` видимо, `ZEPHYR_API_TOKEN` скрыто). **`main()`
   в `__main__.py` перехватывает `--help`/`-h` ДО `load_config()`** — иначе
   `SystemExit` об отсутствии env не дал бы вывести справку; это лёгкая
   самопроверка для install-скриптов.
   **`config.py` сам читает `.env`** как fallback, если переменной нет в
   `os.environ` (путь выбирается через `platform.system()`, должен совпадать
   с тем, что пишет соответствующий install-скрипт) — так секреты не
   дублируются в JSON-конфиге Cline. `os.environ` приоритетнее файла. Тесты
   изолируются от реального `.env` на машине разработчика через autouse-
   фикстуру `no_env_file` в `tests/test_config.py`.

5. Конфиг для подключения через Cline (VS Code) — `cline_mcp_settings.json` (см. README):
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

## Порядок работы

1. ✅ Скелет проекта + HTTP-клиент с Bearer auth + один рабочий тул (`list_executions`) — проверен
   end-to-end через Cline на реальном `CLOUDDEV-C667` (3 items, совпадают с прямым curl-запросом).
2. Перед каждым следующим тулом — разведка реального эндпоинта curl'ом (см. таблицу выше, столбец "не разведано").
3. После разведки — реализовать тул, проверить на реальных данных, только потом переходить к следующему.
4. Не расширять список тулов сверх таблицы без явного запроса.