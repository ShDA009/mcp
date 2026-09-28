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
инстансе** (project `PROJ`, ключи вида `PROJ-T853`/`PROJ-C667`), а
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
  `/Папка`, где 663 ТК лежат в подпапках) дают `[]` при точном совпадении и
  `400` при попытке `~` (contains) — сервер не поддерживает
  префиксный/contains-поиск по `folder` вообще.
- **`folder`-фильтр в `list_test_cases`/`list_cycles` поэтому реализован
  client-side**, не через JQL: сервер делает один запрос с
  `fields=key,folder,name` (sparse fieldset — полные объекты для всего проекта
  PROJ дают ~54с, sparse — ~9с) и `maxResults=5000`, затем фильтрует по
  `folder == prefix or folder.startswith(prefix + "/")` на клиенте
  (`_filter_by_folder_prefix`). Возвращает только лёгкую проекцию
  (`key`/`folder`/`name`), не полные объекты.
- **Батчинг по `KEY_IN_BATCH_SIZE=100` обязателен** для `get_test_cases_batch`/
  `get_cycles_batch`: один запрос `key IN (...)` со всеми 663 ключами `/Папка`
  дал `414 URI Too Long`. Замер: 50 ключей → 1.4с, 100 → 2.3с, 150 → 3.3с,
  200 → 4.1с (линейный рост ~20мс/ключ) — 100 выбран с запасом до
  неизвестной точной границы (она где-то между 200 и 663).
- **Создание кейса — `POST /rest/atm/1.0/testcase`** с `projectKey` строкой.
  Шаги передаются плоско в `testScript.steps` (`type: "STEP_BY_STEP"`), обёртка
  `stepByStepScript` — это контракт `/rest/tests/1.0`, здесь не работает.
  `status` (`Draft`), `priority` (`Normal`) и `majorVersion` проставляет сервер.
- **`/rest/tests/1.0` на инстансе существует**, но подходит не для всего:
  `GET /rest/tests/1.0/testcase/{key}` отвечает только с `?fields=...` (без них
  500), а `POST` туда создаёт кейс с пустым `PLAIN_TEXT`-скриптом — шаги
  теряются. Весь ATM-контракт покрывает `/rest/atm/1.0`, второй базовый путь в
  клиенте не нужен.
- **`PUT /rest/atm/1.0/testcase/{key}` работает как PATCH, а не замена.**
  Проверено: на кейс с objective и одним шагом отправили PUT только с
  `{"name": "..."}` → 200, при чтении name изменился, objective и шаги на
  месте. Read-modify-write для правки кейса не нужен.
- **Сбросить `folder` кейса обратно в корень проекта через `PUT /testcase/{key}`
  нельзя.** Проверено curl: `{"folder": "/"}` → `400 The value / was not found
  for field folder on project ...`; `{"folder": null}` → `200`, но значение не
  меняется (PATCH игнорирует `null` как «поле не передано», не как «очистить»).
  `update_test_case` поэтому валидирует `folder` через `_validate_folder_path`
  и отвергает `"/"` клиентом же — сервер всё равно откажет.
  Jira UI сбрасывает папку через другой эндпоинт: `PUT /rest/tests/1.0/testcase/{numericId}`
  с `{"folderId": null}` (числовой id, не путь строкой) — это отдельный, ранее
  отвергнутый контракт (см. выше: `/rest/tests/1.0` теряет шаги на POST), клиент
  его не использует. Сброс в корень через `/rest/atm/1.0/testcase` невозможен.
- **Запись execution-результата — `PUT /testrun/{key}/testcase/{caseKey}/testresult`**
  (единичный путь, `Allow: OPTIONS,POST,PUT`), не `POST /testrun/{key}/testresults`
  (множественный, `Allow: OPTIONS,HEAD,POST,GET` — там PUT не поддерживается).
  Разница критична и подтверждена curl:
  - `POST /testresults` создаёт **новую** execution-запись при каждом вызове —
    не обновляет предыдущую (история копится, `id`/`key` каждый раз новые), и
    если `testCaseKey` не входит в items рана, молча добавляет его туда. Для
    «тестировщик проставил результат» это неправильный эндпоинт: агент,
    вызывающий его повторно (или после первого авто-статуса рана), плодит
    дубли execution на один и тот же кейс в одном ране — обнаружено на живом
    инстансе, не просто теоретический риск.
  - `PUT /testrun/{key}/testcase/{caseKey}/testresult` обновляет **существующую**
    execution кейса in place, без дублей — ровно то, что делает тестировщик
    руками. Требует, чтобы кейс уже был item рана (добавлен через
    `create_test_run`/`add_test_cases_to_run`); если нет — `400 No test
    execution found on test run ... for the informed parameters.`
  - Тело `PUT` — один объект (не список): `{"status": ..., "comment"?: ...,
    "scriptResults"?: [{"index": int, "status": ...}]}`, `testCaseKey` в URL,
    не в теле.
  - `status` обязателен, сервер валидирует его против значений проекта
    (невалидный → `400 The value X was not found for field status on project ...`).
  - `scriptResults[].actualResult` и `.result` оба отвергаются `500
    Unrecognized field` (`TestScriptResultDTO` не десериализует лишние поля —
    старый Jackson без `@JsonIgnoreProperties`) — единственные принятые поля
    шага: `index`, `status`. Текстового actual-result на уровне шага не
    существует вообще (проверено на обоих путях, `POST` и `PUT`).
- **Успешный `PUT` кейса возвращает пустое тело**, не JSON.
- **`POST /rest/atm/1.0/folder` принимает путь целиком в поле `name`** и он
  обязан начинаться со слэша. Без слэша сервер отвечает 400 с текстом `Field name
  must begin with forward slash and backslashes are not allowed.` Отдельного
  `parentId` в API нет, вложенность выражается самим путём. Ответ:
  `201 {"id": <число>}` — числовой id, не ключ.
- **`POST /rest/atm/1.0/testrun` принимает `items` прямо при создании**:
  `{"projectKey":"PROJ","name":"...","items":[{"testCaseKey":"PROJ-T853"}]}` →
  `201 {"key":"PROJ-C818"}`. Цикл с кейсами создаётся одним запросом.
- **`PUT /rest/atm/1.0/testrun/{key}` не работает на живом инстансе** — 500 с
  пустым телом на любой пейлоад (проверено: только `{"name": ...}`, только
  `{"items": [...]}`, полный объект цикла, на свежем пустом цикле и на цикле с
  items — везде 500). Обновлять items цикла через PUT нельзя.
- **Добавление кейсов в существующий цикл — `POST /rest/atm/1.0/testrun/{key}/testresults`
  с JSON-массивом** `[{"testCaseKey":"PROJ-T853"}]` (не одиночным объектом —
  одиночный объект даёт 500 `Can not deserialize instance of java.util.ArrayList`).
  Ответ `201 [{"id": ...}]`. Каждый кейс добавляется в items рана со статусом
  `Not Executed` и получает execution-запись. Повторный POST того же кейса
  плодит дубли execution — поэтому `add_test_cases_to_run` сначала читает items
  рана и шлёт только новые ключи.
- **Архивация/удаление живут в `/rest/tests/1.0/` и работают с числовыми id,
  а не ключами.** Числовой id получается через
  `GET /rest/tests/1.0/testcase/{key}?fields=id` (для ТК) и
  `GET /rest/tests/1.0/testrun/{key}?fields=id` (для циклов) — ответ
  `{"id": <число>, "key": ...}`. Проверено на живом инстансе:
  - Архив ТК: `POST /rest/tests/1.0/testcase/bulk/archive` с массивом id →
    `200`, кейс получает `archived: true` и исчезает из ATM-поиска.
  - Восстановление ТК: `POST /rest/tests/1.0/testcase/bulk/unarchive` с
    массивом id → `200`, кейс снова `archived: false`.
  - Удаление цикла: `POST /rest/tests/1.0/testrun/bulk/delete` с массивом id →
    `200`, после удаления цикл возвращает `404`.
  - `PUT /rest/tests/1.0/testrun/archive` с `{"id": ...}` возвращает `200`, но
    цикл НЕ скрывается из списков — архивация циклов через API не работает
    (в отличие от ТК), поэтому для циклов используется удаление.
  - `PUT /testcase/{key}` с `{"archived": true}` возвращает `200`, но поле
    игнорируется — кейс не архивируется.
- **НЕ проверено: формат тела загрузки картинок на
  `/rest/tests/1.0/attachment/embeddedimage`.** `OPTIONS` отвечает 200 —
  эндпоинт существует, но tool для картинок не реализован.
- **`/v2/...` (Zephyr Scale Cloud) на self-hosted отдаёт 302** — готовые MCP
  под Cloud с подменой хоста не заработают.
- **`GET /rest/atm/1.0/folder` (коллекция) даёт 500**, но при создании кейса
  поле `folder` работает и принимает путь строкой (`/Папка/Подпапка`) —
  подтверждено curl, `201`. Числовой `folderId` не нужен; папка должна
  существовать, сервер её не создаёт.

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
- Пишущие запросы (`POST`, `PUT`) не ретраятся на 429 (`_request(...,
  retry_on_429=False)`): POST в ATM неидемпотентен, и повтор после 429 может
  создать дубль тест-кейса, если запрос уже прошёл на бэкенде до ответа
  прокси/rate-limit'а.
