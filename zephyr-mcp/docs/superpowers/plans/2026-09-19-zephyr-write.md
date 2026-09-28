# create_test_case Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в `zephyr-mcp` tool `create_test_case`, создающий тест-кейс в Zephyr Scale (ATM), доступный только при включённом `ZEPHYR_ALLOW_WRITE`.

**Architecture:** Три слоя без изменения границ: `config.py` читает необязательный флаг, `client.py` получает метод `create_test_case` поверх существующего `_request` (он уже умеет POST через `json=`, ретраи 429, таймаут 15s и разбор ошибок — нового транспорта не нужно), `tools.py` регистрирует пишущий tool под условием. Сборка payload вынесена в чистую функцию `_build_test_case_payload`, чтобы тестироваться без сети, как остальные хелперы клиента.

**Tech Stack:** Python 3.14, httpx, FastMCP (`mcp.server.fastmcp`), pytest, uv.

## Global Constraints

- Тесты без сети, на чистых функциях, в `tests/`. Запуск: `uv run pytest tests/ -v`.
- `ZEPHYR_API_TOKEN` не логируется и не попадает в repr (в `Config` он уже `field(repr=False)`).
- Ошибки не проглатываются: всё, что вернул сервер, поднимается как `ZephyrError` через существующий `_handle_response`.
- Комментарии в коде — только там, где уже приняты в файле; в `client.py` они на английском, в `config.py` на русском. Следовать файлу.
- Спека: [2026-09-19-zephyr-write-design.md](../specs/2026-09-19-zephyr-write-design.md). Контракт ATM подтверждён curl на живом инстансе.
- Ветка `feature/zephyr-write` уже создана, спека закоммичена. Все коммиты — в неё.

---

### Task 1: Флаг `ZEPHYR_ALLOW_WRITE` в конфиге

**Files:**
- Modify: `src/zephyr_mcp/config.py:33-51`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: ничего (первая задача).
- Produces: `Config.allow_write: bool` — поле датакласса, по умолчанию `False`. Читают Task 3 и Task 4.

- [ ] **Step 1: Написать падающие тесты**

Добавить в конец `tests/test_config.py`. В файле уже есть autouse-фикстура `no_env_file`, изолирующая тесты от реального `~/.config/zephyr-mcp/.env` — она применится автоматически.

```python
def test_allow_write_defaults_to_false(monkeypatch):
    monkeypatch.setenv("ZEPHYR_BASE_URL", "https://jira.example.com")
    monkeypatch.setenv("ZEPHYR_API_TOKEN", "t")
    monkeypatch.delenv("ZEPHYR_ALLOW_WRITE", raising=False)
    assert load_config().allow_write is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_allow_write_enabled_by_truthy_values(monkeypatch, value):
    monkeypatch.setenv("ZEPHYR_BASE_URL", "https://jira.example.com")
    monkeypatch.setenv("ZEPHYR_API_TOKEN", "t")
    monkeypatch.setenv("ZEPHYR_ALLOW_WRITE", value)
    assert load_config().allow_write is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", "", "  "])
def test_allow_write_disabled_by_falsy_values(monkeypatch, value):
    monkeypatch.setenv("ZEPHYR_BASE_URL", "https://jira.example.com")
    monkeypatch.setenv("ZEPHYR_API_TOKEN", "t")
    monkeypatch.setenv("ZEPHYR_ALLOW_WRITE", value)
    assert load_config().allow_write is False


def test_allow_write_missing_creds_still_fails_fast(monkeypatch):
    monkeypatch.delenv("ZEPHYR_BASE_URL", raising=False)
    monkeypatch.setenv("ZEPHYR_ALLOW_WRITE", "1")
    with pytest.raises(SystemExit, match="ZEPHYR_BASE_URL"):
        load_config()
```

Импорты `pytest` и `load_config` в шапке файла уже есть — добавлять ничего не нужно.

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_config.py -v -k allow_write`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'allow_write'`.

- [ ] **Step 3: Реализация**

В `config.py` после `_read_env_file` добавить константу:

```python
_FALSY = {"0", "false", "no", "off", ""}
```

В датакласс `Config` добавить поле (последним, у него есть значение по умолчанию):

```python
@dataclass
class Config:
    base_url: str
    api_token: str = field(repr=False)
    allow_write: bool = False
```

В `load_config()` перед `return` добавить чтение — той же схемой «env приоритетнее файла», что и обязательные переменные:

```python
    # Пишущие tools по умолчанию выключены: Zephyr общий для команды, и
    # обновление сервера не должно молча давать агенту право создавать кейсы.
    raw_allow_write = (
        os.environ.get("ZEPHYR_ALLOW_WRITE")
        or file_values.get("ZEPHYR_ALLOW_WRITE", "")
    ).strip().lower()
```

И передать в конструктор:

```python
    return Config(
        base_url=values["ZEPHYR_BASE_URL"].rstrip("/"),
        api_token=values["ZEPHYR_API_TOKEN"],
        allow_write=raw_allow_write not in _FALSY,
    )
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS, включая ранее существовавшие тесты конфига.

- [ ] **Step 5: Коммит**

```bash
git add src/zephyr_mcp/config.py tests/test_config.py
git commit -m "feat(zephyr-mcp): флаг ZEPHYR_ALLOW_WRITE, по умолчанию выключен"
```

---

### Task 2: Сборка payload и метод клиента

**Files:**
- Modify: `src/zephyr_mcp/client.py` (новая чистая функция рядом с `_escape_jql`/`_chunk`; новый метод рядом с `get_test_case`)
- Test: `tests/test_client.py`

**Interfaces:**
- Consumes: существующие `ZephyrClient._request`, `API_BASE_PATH`, `ZephyrError`.
- Produces:
  - `_build_test_case_payload(project_key: str, name: str, steps: list[dict], objective: str | None, precondition: str | None, folder: str | None, labels: list[str] | None) -> dict` — чистая функция, тестируется напрямую.
  - `ZephyrClient.create_test_case(project_key, name, steps, objective=None, precondition=None, folder=None, labels=None) -> Any` — возвращает ответ сервера как есть, например `{"key": "PROJ-T123"}`. Вызывает Task 3.

- [ ] **Step 1: Написать падающие тесты**

Добавить в конец `tests/test_client.py`, импорт `_build_test_case_payload` — в существующий блок импортов из `zephyr_mcp.client`.

```python
def test_build_payload_minimal_has_only_required_fields():
    payload = _build_test_case_payload(
        "PROJ", "Имя", [{"description": "шаг"}], None, None, None, None
    )
    assert payload == {
        "projectKey": "PROJ",
        "name": "Имя",
        "testScript": {
            "type": "STEP_BY_STEP",
            "steps": [{"index": 0, "description": "шаг"}],
        },
    }


def test_build_payload_never_sends_server_defaults():
    payload = _build_test_case_payload(
        "PROJ", "Имя", [{"description": "шаг"}], None, None, None, None
    )
    assert "status" not in payload
    assert "priority" not in payload
    assert "majorVersion" not in payload


def test_build_payload_indexes_steps_sequentially():
    steps = [
        {"description": "первый"},
        {"description": "второй", "expectedResult": "ок"},
        {"description": "третий", "testData": "данные"},
    ]
    payload = _build_test_case_payload("PROJ", "Имя", steps, None, None, None, None)
    assert [s["index"] for s in payload["testScript"]["steps"]] == [0, 1, 2]
    assert payload["testScript"]["steps"][1]["expectedResult"] == "ок"
    assert payload["testScript"]["steps"][2]["testData"] == "данные"


def test_build_payload_ignores_caller_supplied_index():
    steps = [{"description": "a", "index": 99}, {"description": "b", "index": 42}]
    payload = _build_test_case_payload("PROJ", "Имя", steps, None, None, None, None)
    assert [s["index"] for s in payload["testScript"]["steps"]] == [0, 1]


def test_build_payload_drops_empty_optional_step_fields():
    steps = [{"description": "шаг", "expectedResult": "", "testData": None}]
    payload = _build_test_case_payload("PROJ", "Имя", steps, None, None, None, None)
    assert payload["testScript"]["steps"][0] == {"index": 0, "description": "шаг"}


def test_build_payload_includes_optional_fields_when_set():
    payload = _build_test_case_payload(
        "PROJ", "Имя", [{"description": "шаг"}],
        "цель", "предусловие", "/Папка/Подпапка", ["smoke"],
    )
    assert payload["objective"] == "цель"
    assert payload["precondition"] == "предусловие"
    assert payload["folder"] == "/Папка/Подпапка"
    assert payload["labels"] == ["smoke"]


def test_build_payload_drops_empty_optional_fields():
    payload = _build_test_case_payload(
        "PROJ", "Имя", [{"description": "шаг"}], "", None, "", [],
    )
    assert "objective" not in payload
    assert "precondition" not in payload
    assert "folder" not in payload
    assert "labels" not in payload


def test_build_payload_passes_html_through_unchanged():
    html = "<ul><li>первый</li></ul>"
    payload = _build_test_case_payload(
        "PROJ", "Имя", [{"description": html}], html, None, None, None
    )
    assert payload["testScript"]["steps"][0]["description"] == html
    assert payload["objective"] == html


def test_build_payload_rejects_empty_steps():
    with pytest.raises(ZephyrError, match="at least one step"):
        _build_test_case_payload("PROJ", "Имя", [], None, None, None, None)


def test_build_payload_rejects_step_without_description():
    with pytest.raises(ZephyrError, match="description"):
        _build_test_case_payload(
            "PROJ", "Имя", [{"expectedResult": "ок"}], None, None, None, None
        )
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_client.py -v -k build_payload`
Expected: FAIL при сборе — `ImportError: cannot import name '_build_test_case_payload'`.

- [ ] **Step 3: Реализация**

В `client.py` добавить чистую функцию рядом с `_escape_jql` (после неё, до `_chunk`):

```python
def _build_test_case_payload(
    project_key: str,
    name: str,
    steps: list[dict],
    objective: str | None,
    precondition: str | None,
    folder: str | None,
    labels: list[str] | None,
) -> dict:
    # ATM stores step-by-step scripts flat in testScript.steps; the
    # stepByStepScript wrapper belongs to /rest/tests/1.0 and is rejected here.
    # status, priority and majorVersion are filled in by the server.
    if not steps:
        raise ZephyrError("test case needs at least one step")

    payload: dict[str, Any] = {"projectKey": project_key, "name": name}

    if objective:
        payload["objective"] = objective
    if precondition:
        payload["precondition"] = precondition
    if folder:
        payload["folder"] = folder
    if labels:
        payload["labels"] = labels

    built_steps = []
    for index, step in enumerate(steps):
        description = step.get("description")
        if not description:
            raise ZephyrError(f"step {index} has no description")
        built: dict[str, Any] = {"index": index, "description": description}
        if step.get("expectedResult"):
            built["expectedResult"] = step["expectedResult"]
        if step.get("testData"):
            built["testData"] = step["testData"]
        built_steps.append(built)

    payload["testScript"] = {"type": "STEP_BY_STEP", "steps": built_steps}
    return payload
```

Метод в `ZephyrClient` — после `get_test_case` (строка 47), чтобы чтение и запись кейса лежали рядом:

```python
    def create_test_case(
        self,
        project_key: str,
        name: str,
        steps: list[dict],
        objective: str | None = None,
        precondition: str | None = None,
        folder: str | None = None,
        labels: list[str] | None = None,
    ) -> Any:
        payload = _build_test_case_payload(
            project_key, name, steps, objective, precondition, folder, labels
        )
        return self._request("POST", API_BASE_PATH + "/testcase", json=payload)
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/test_client.py -v`
Expected: PASS, включая 27 ранее существовавших тестов.

- [ ] **Step 5: Коммит**

```bash
git add src/zephyr_mcp/client.py tests/test_client.py
git commit -m "feat(zephyr-mcp): ZephyrClient.create_test_case и сборка ATM-payload"
```

---

### Task 3: Регистрация tool под флагом

**Files:**
- Modify: `src/zephyr_mcp/tools.py:6` (сигнатура), конец файла (новый tool)
- Modify: `src/zephyr_mcp/__main__.py` (вызов `register_tools`)
- Test: `tests/test_tools.py` (создать)

**Interfaces:**
- Consumes: `Config.allow_write` (Task 1), `ZephyrClient.create_test_case` (Task 2).
- Produces: `register_tools(mcp: FastMCP, client: ZephyrClient, config: Config) -> None` — сигнатура с третьим параметром.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_tools.py`. `FastMCP.list_tools()` — корутина, поэтому обёрнута в `asyncio.run`; `SimpleNamespace` вместо клиента достаточно, ни один tool при регистрации не вызывается.

```python
import asyncio
from types import SimpleNamespace

from mcp.server.fastmcp import FastMCP

from zephyr_mcp.config import Config
from zephyr_mcp.tools import register_tools

READ_ONLY_TOOLS = {
    "list_executions",
    "get_execution",
    "get_test_case",
    "list_cycles",
    "list_test_cases",
    "get_test_cases_batch",
    "get_cycles_batch",
    "get_project",
    "list_projects",
}


def _tool_names(*, allow_write: bool) -> set[str]:
    mcp = FastMCP("test")
    config = Config(base_url="https://jira.example.com", api_token="t", allow_write=allow_write)
    register_tools(mcp, SimpleNamespace(), config)
    return {tool.name for tool in asyncio.run(mcp.list_tools())}


def test_write_tool_absent_when_flag_disabled():
    assert _tool_names(allow_write=False) == READ_ONLY_TOOLS


def test_write_tool_registered_when_flag_enabled():
    assert _tool_names(allow_write=True) == READ_ONLY_TOOLS | {"create_test_case"}
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `uv run pytest tests/test_tools.py -v`
Expected: FAIL — `TypeError: register_tools() takes 2 positional arguments but 3 were given`.

- [ ] **Step 3: Реализация**

В `tools.py` изменить импорты и сигнатуру:

```python
from mcp.server.fastmcp import FastMCP

from zephyr_mcp.client import ZephyrClient
from zephyr_mcp.config import Config


def register_tools(mcp: FastMCP, client: ZephyrClient, config: Config) -> None:
```

В конец `register_tools` (после `list_projects`, с тем же отступом) добавить:

```python
    if not config.allow_write:
        return

    @mcp.tool()
    def create_test_case(
        project_key: str,
        name: str,
        steps: list[dict],
        objective: str | None = None,
        precondition: str | None = None,
        folder: str | None = None,
        labels: list[str] | None = None,
    ) -> dict:
        """Create a Zephyr Scale test case, e.g. project_key=PROJ.

        steps is a list of dicts: description (required), expectedResult and
        testData (optional). Step order is taken from the list.

        Text fields (objective, precondition, and each step's description,
        expectedResult, testData) are stored and rendered as HTML. Pass ready
        HTML: <ul><li>item</li></ul> for lists, <b> for emphasis, <br/> for a
        line break. Plain text without tags is fine too; escape literal < and >
        as &lt; and &gt;.

        folder is an existing folder path, e.g. "/Папка/Подпапка". The folder is
        not created — omit it to put the test case in the project root.

        Returns the created key, e.g. {"key": "PROJ-T123"}.
        """
        return client.create_test_case(
            project_key, name, steps, objective, precondition, folder, labels
        )
```

Ранний `return` вместо `if config.allow_write:` с вложенным блоком — так у tool остаётся тот же уровень отступа, что у остальных девяти.

В `__main__.py:27` заменить вызов, используя уже созданную переменную `cfg` (строка 23), повторно конфиг не читать:

```python
    register_tools(mcp, client, cfg)
```

Там же дополнить текст `--help` (строка 20) — по образцу `outlook-mcp`, где флаг записи упомянут в справке:

```python
            "Required env: ZEPHYR_BASE_URL, ZEPHYR_API_TOKEN.\n"
            "Optional: ZEPHYR_ALLOW_WRITE=1 enables the test-case-write tool."
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/ -v`
Expected: PASS — все тесты, включая новый файл.

- [ ] **Step 5: Проверить, что сервер стартует**

Run: `uv run zephyr-mcp --help`
Expected: справка печатается, в ней упомянут `ZEPHYR_ALLOW_WRITE`, ошибок импорта нет. Команда завершается без открытия stdio-сессии — этим же вызовом пользуются install-скрипты.

Run: `ZEPHYR_ALLOW_WRITE=1 uv run zephyr-mcp --help`
Expected: то же самое; флаг не ломает режим самопроверки (справка печатается до `load_config()`).

- [ ] **Step 6: Коммит**

```bash
git add src/zephyr_mcp/tools.py src/zephyr_mcp/__main__.py tests/test_tools.py
git commit -m "feat(zephyr-mcp): tool create_test_case под ZEPHYR_ALLOW_WRITE"
```

---

### Task 4: Документация

**Files:**
- Modify: `README.md` (таблица tools, раздел про переменные окружения)
- Modify: `CLAUDE.md` (раздел «Грабли API»)
- Modify: `install/README.md` (упоминание флага)
- Modify: `../CHANGELOG.md` (общий changelog репозитория)

**Interfaces:**
- Consumes: готовую реализацию Task 1-3.
- Produces: ничего (терминальная задача).

- [ ] **Step 1: README.md — строка в таблице tools**

Добавить последней строкой таблицы:

```markdown
| `create_test_case` | `project_key`, `name`, `steps`, `objective=None`, `precondition=None`, `folder=None`, `labels=None` | Создать тест-кейс. Доступен только при `ZEPHYR_ALLOW_WRITE=1`. `steps` — список словарей (`description` обязателен, `expectedResult`/`testData` опциональны). Текстовые поля сохраняются как HTML. `folder` — путь существующей папки, папка не создаётся |
```

- [ ] **Step 2: README.md — раздел про переменные**

После описания `ZEPHYR_BASE_URL`/`ZEPHYR_API_TOKEN` добавить:

```markdown
- `ZEPHYR_ALLOW_WRITE` — необязательная, **по умолчанию выключена**.
  Включается значением `1`/`true`/`yes`/`on`. Пока выключена, пишущие tools
  не регистрируются: клиент их не видит и вызвать не может. Zephyr общий для
  команды, поэтому запись включается осознанно, в отличие от `outlook-mcp`,
  где `EWS_ALLOW_WRITE` включён по умолчанию.
```

- [ ] **Step 3: CLAUDE.md — результаты curl в «Грабли API»**

Добавить в конец списка:

```markdown
- **Создание кейса — `POST /rest/atm/1.0/testcase`** с `projectKey` строкой.
  Шаги передаются плоско в `testScript.steps` (`type: "STEP_BY_STEP"`), обёртка
  `stepByStepScript` — это контракт `/rest/tests/1.0`, здесь не работает.
  `status` (`Draft`), `priority` (`Normal`) и `majorVersion` проставляет сервер.
- **`/rest/tests/1.0` на инстансе существует**, но подходит не для всего:
  `GET /rest/tests/1.0/testcase/{key}` отвечает только с `?fields=...` (без них
  500), а `POST` туда создаёт кейс с пустым `PLAIN_TEXT`-скриптом — шаги
  теряются. Весь ATM-контракт покрывает `/rest/atm/1.0`, второй базовый путь в
  клиенте не нужен.
- **`/v2/...` (Zephyr Scale Cloud) на self-hosted отдаёт 302** — готовые MCP
  под Cloud с подменой хоста не заработают.
- **`GET /rest/atm/1.0/folder` (коллекция) даёт 500**; поведение при создании
  кейса с полем `folder` не проверялось.
```

- [ ] **Step 4: install/README.md**

Добавить в раздел про `.env` строку о том, что `ZEPHYR_ALLOW_WRITE=1` в `~/.config/zephyr-mcp/.env` (или `%USERPROFILE%\.zephyr-mcp\.env` на Windows) включает создание тест-кейсов, по умолчанию сервер read-only. Установочные скрипты переменную не спрашивают.

- [ ] **Step 5: CHANGELOG.md в корне репозитория**

Добавить запись новой датой, сверху, по формату существующих записей (см. запись `2026-08-16 — outlook-mcp 0.2.0`): заголовок с сервером и версией, разделы «Добавлено» и при необходимости «Изменено». Упомянуть `create_test_case`, флаг `ZEPHYR_ALLOW_WRITE` и то, что он выключен по умолчанию.

- [ ] **Step 6: Прогнать тесты целиком**

Run: `uv run pytest tests/ -v`
Expected: PASS.

- [ ] **Step 7: Коммит**

```bash
git add README.md CLAUDE.md install/README.md ../CHANGELOG.md
git commit -m "docs(zephyr-mcp): create_test_case и ZEPHYR_ALLOW_WRITE"
```

---

## Проверка на живом инстансе (после Task 4)

Автотесты сети не касаются, поэтому контракт проверяется вручную один раз.

Выполнено: поле `folder` принимает путь строкой, сервер ответил `201`. Числовой `folderId` не понадобился, правок в коде нет.

Созданные при проверке кейсы удалить: `DELETE /rest/atm/1.0/testcase/{key}`.

## Вне области

Не входит в этот план (см. «Дальнейшие версии» спеки): `update_test_case`,
`create_folder`, `create_test_run`, `add_test_cases_to_run`, `upload_image`,
удаление кейсов, белый список HTML-тегов.
