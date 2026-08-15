# План: запись в календарь (create/update/delete)

Спек: [docs/superpowers/specs/2026-08-15-calendar-write-design.md](../docs/superpowers/specs/2026-08-15-calendar-write-design.md)
Дата: 2026-08-15
Режим: **только git** (`gh` не установлен) — ветки без PR-команд.
Базовая ветка: `master`

## Общий контекст для всех шагов

Проект `outlook-mcp` — MCP-сервер к on-prem Exchange по EWS (exchangelib
5.6.0, NTLM). Рабочая директория всех команд:
`/Users/shda/home/mot/git/AI-workspace/mcp/outlook-mcp`.

Обязательно к прочтению перед любым шагом: `CLAUDE.md` (раздел «Важные
решения») и спек выше. Три правила проекта, нарушение которых ломает
работу молча:

1. **Фейки в тестах обязаны повторять реальный контракт exchangelib**, а
   не то, как API «логично выглядит». Баг с `WorkingPeriod.weekdays` не
   поймали ~60 зелёных тестов именно из-за фейка с удобным
   представлением. Типы полей сверять с исходниками библиотеки.
2. **Ошибки не пробрасываются через MCP** — каждый tool ловит
   `OutlookMcpError` и возвращает `exc.to_dict()`.
3. **Никогда `list(qs)` без среза** на запросах к папкам/календарю.

Тесты: `uv run pytest` (весь набор), `uv run pytest tests/test_X.py -q`
(один файл).

Шаги 1–3 и 7 — модель `default`. Шаги 4, 5 — `strongest` (архитектурные
решения по времени и recurrence). Шаг 6 — `strongest` (риск с
регистрацией tools). Шаг 8 — ручной, выполняет пользователь.

## Граф зависимостей

```
1 (config+errors) ──┬─→ 4 (create) ──┐
                    │                 ├─→ 6 (регистрация tools) ─→ 7 (docs) ─→ 8 (живая проверка)
2 (общие хелперы) ──┼─→ 5 (update+delete) ┘
                    │
3 (фейки в conftest)┘
```

**Параллельно:** только шаги 1, 2, 3 — разные файлы, общих зависимостей
нет.

**Шаги 4 и 5 строго последовательны** (4 → 5), несмотря на общие
зависимости: оба дописывают `calendar_write.py`, созданный в шаге 2.
Параллельная работа даст конфликт в одном файле.

---

## Шаг 1. `EWS_ALLOW_WRITE` в конфиге + `PermissionDeniedError`

**Модель:** default
**Зависимости:** нет
**Ветка:** `feat/calendar-write-config`

### Context brief

`config.py` читает переменные из `os.environ` с fallback на файл
(`~/.config/outlook-mcp/.env` на macOS/Linux). `Config.__init__` не
валидирует — валидация ленивая, в `validate()`, вызывается при первом
обращении к tool. Это часть критерия готовности: сервер обязан стартовать
и отдавать `tools/list` без реальных кредов.

`errors.py` — плоский список классов, каждый со строковым `code`, база
`OutlookMcpError.to_dict()`.

### Task list

1. В `Config.__init__` добавить `self.allow_write` — булево, читается из
   `EWS_ALLOW_WRITE` тем же `_get`. **По умолчанию включено.**
   Выключается значениями `0`, `false`, `no` (регистронезависимо, с
   `.strip()`). Любое другое значение (включая пустую строку и
   отсутствие переменной) → `True`.
2. **Не добавлять** `EWS_ALLOW_WRITE` в список обязательных в
   `validate()` — переменная необязательна.
3. В `errors.py` добавить `PermissionDeniedError(OutlookMcpError)` с
   `code = "permission_denied"`.
4. В `tests/test_config.py` добавить тесты: дефолт (переменной нет) →
   `True`; `"0"`, `"false"`, `"FALSE"`, `"no"` → `False`; `"1"`,
   `"true"`, мусор → `True`. Учесть autouse-фикстуру `no_env_file`,
   которая уже подменяет `_ENV_FILE` на несуществующий путь.
5. В `tests/test_errors.py` добавить тест сериализации
   `PermissionDeniedError.to_dict()`.

### Verification

```bash
uv run pytest tests/test_config.py tests/test_errors.py -q
```

### Exit criteria

- Тесты обоих файлов зелёные.
- `Config().allow_write is True` без переменной окружения.
- `validate()` не требует `EWS_ALLOW_WRITE`.

---

## Шаг 2. Общие хелперы: парсинг времени и доступ к item

**Модель:** default
**Зависимости:** нет
**Ветка:** `feat/calendar-write-helpers`

### Context brief

`calendar_service.py` содержит `_fetch_one` (строка 61) и
`_find_by_id_in_calendar` (строка 82) — получение item по
`(item_id, changekey)` с fallback-сканом при устаревшем ChangeKey. Этот
код уже стоил отладки на живом сервере, дублировать его нельзя.
`calendar_service.py` остаётся владельцем, новый модуль импортирует.

`server.py` содержит `_validate_emails` (строка 50) — нормализация,
дедупликация, лимит 20 адресов. Пишущим tools она нужна тоже.

Времена в проекте конвертируются через `ZoneInfo(config.timezone)`, но
для вызовов, уходящих в EWS-сервисы, нужен `EWSTimeZone.from_zoneinfo(tz)`
(см. CLAUDE.md про `find_free_slots`) — голый `ZoneInfo` там не работает.

### Task list

1. Создать `src/outlook_mcp/calendar_write.py` с одним публичным
   хелпером `parse_datetime(value: str, field_name: str, config: Config)
   -> datetime`:
   - парсит ISO-строку через `datetime.fromisoformat`;
   - **naive** (без tzinfo) → трактуется как локальное время
     `config.timezone`, привязывается через
     `EWSTimeZone.from_zoneinfo(ZoneInfo(config.timezone))`;
   - **aware** (с offset или `Z`) → конвертируется в ту же
     `EWSTimeZone`, исходный момент сохраняется;
   - при ошибке парсинга — `InvalidArgumentError` с примером формата
     (`"2026-08-20T15:00"`), по образцу `_parse_date` в `server.py`.
   - `datetime.fromisoformat` в Python 3.11+ понимает `Z`; проверить
     версию в `pyproject.toml` и, если ниже, обработать `Z` явно.
2. Добавить `parse_date_only(value, field_name) -> date` для all-day
   (принимает как `"2026-08-20"`, так и полную ISO-строку, берёт из неё
   дату).
3. **Переместить** `_validate_emails` и `_MAX_FREE_BUSY_EMAILS` из
   `server.py` в `calendar_write.py` как публичную `validate_emails`;
   в `server.py` импортировать её и оставить алиас
   `_validate_emails = validate_emails`, чтобы существующие тесты в
   `tests/test_server.py` (`test_validate_emails_*`) не сломались.
4. В `calendar_service.py` переименовать `_fetch_one` →
   `fetch_one` и `_find_by_id_in_calendar` → `find_by_id_in_calendar`
   (публичные, т.к. используются из другого модуля), обновить вызовы
   внутри файла. **Существующее поведение не менять.**
5. Тесты `parse_datetime` / `parse_date_only` — в новый
   `tests/test_calendar_write.py`: naive → нужная зона; aware с `+03:00`
   и `Z` → тот же момент; мусор → `InvalidArgumentError`.

### Verification

```bash
uv run pytest -q
```

### Exit criteria

- Весь существующий набор тестов зелёный (переименования ничего не
  сломали).
- `parse_datetime("2026-08-20T15:00", ...)` при `timezone=Europe/Moscow`
  даёт 15:00 MSK, а `parse_datetime("2026-08-20T12:00Z", ...)` — тот же
  момент.

---

## Шаг 3. Фейки для записи в `conftest.py`

**Модель:** default
**Зависимости:** нет
**Ветка:** `feat/calendar-write-fakes`

### Context brief

`tests/conftest.py` уже содержит `FakeCalendarItem` с полями `is_all_day`,
`type`, `organizer`, `required_attendees`, `optional_attendees` и фабрику
`make_event(...)`. Чего нет — методов записи: `save()`, `delete()`,
`refresh()`.

Действует правило проекта №1 (см. общий контекст): фейк обязан повторять
реальный контракт exchangelib. Перед написанием — свериться с
исходниками `exchangelib/items/calendar_item.py` по сигнатурам
`save(update_fields=..., send_meeting_invitations=...)` и
`delete(send_meeting_cancellations=...)`, а также с
`exchangelib/properties.py` по `Attendee`/`Mailbox`.

### Task list

1. Добавить в `FakeCalendarItem` методы `save(**kwargs)` и
   `delete(**kwargs)`, которые **записывают полученные kwargs** в поля
   `saved_with` / `deleted_with` (списки вызовов) и не делают ничего
   больше. Тесты должны иметь возможность проверить, какое именно
   значение `send_meeting_invitations` ушло.
2. Сверить с исходниками exchangelib названия kwargs
   (`send_meeting_invitations` у `save`, `send_meeting_cancellations` у
   `delete`) и допустимые значения-константы
   (`SEND_TO_NONE`, `SEND_ONLY_TO_ALL`, `SEND_TO_ALL_AND_SAVE_COPY` —
   проверить точные имена и модуль импорта).
3. Добавить фабрику `make_writable_event(...)` (или расширить
   `make_event` параметром) с настраиваемыми `organizer`, `type`
   (`Single` / `Occurrence` / `Exception` / `RecurringMaster`).
4. Добавить фейк аккаунта с `calendar`, пригодный для `create` (объект,
   у которого создаваемый item получает `.save()`).
5. Добавить в **`tests/test_conftest_fakes.py`** (отдельный файл —
   `tests/test_calendar_write.py` принадлежит шагу 2, иначе шаги
   перестают быть параллельными) самопроверку фейка: вызвать
   `save(send_meeting_invitations=...)` и `delete(...)` напрямую и
   убедиться, что kwargs записались в `saved_with` / `deleted_with`. Без
   этого теста фейк не доказан рабочим, а exit criteria шага не
   проверяемы командой.

### Verification

```bash
uv run pytest -q
```

### Exit criteria

- Существующие тесты зелёные (изменения аддитивные).
- Самопроверка фейка из п. 5 проходит.
- В комментарии рядом с `save`/`delete` зафиксированы точные имена
  kwargs и констант из exchangelib с указанием модуля-источника.

---

## Шаг 4. `create_event` — сервисная логика

**Модель:** strongest
**Зависимости:** шаги 1, 2, 3
**Ветка:** `feat/calendar-write-create`

### Context brief

Спек, раздел «`create_event`». Хелперы `parse_datetime`,
`parse_date_only`, `validate_emails` уже в `calendar_write.py` (шаг 2).
`PermissionDeniedError` уже в `errors.py` (шаг 1). Фейки готовы (шаг 3).

Ответ обязан совпадать по форме с `get_event` — использовать
`format_event_details(item, config.timezone)` из `formatting.py`, плюс
поле `invitations_sent`.

### Task list

1. Реализовать в `calendar_write.py` функцию `create_event(account,
   config, *, subject, start, end, attendees, optional_attendees,
   location, body, all_day, send_invitations, recurrence) -> dict`.
2. Валидация **до любого обращения к EWS**: непустой `subject`;
   `recurrence is not None` → `InvalidArgumentError` («повторяющиеся
   встречи пока не поддерживаются»); адреса через `validate_emails`;
   `end > start` (для all-day `end >= start`).
3. All-day: при `all_day=True` из `start`/`end` берутся только даты
   (`parse_date_only`), время игнорируется, ставится `is_all_day=True`.
   `end` — **включительный** день: пользователь говорит «с 20 по 22».
   Как именно включительный день транслируется в EWS, проверяется на
   живом сервере (шаг 8) — здесь реализовать по документации exchangelib
   и **пометить место комментарием** как требующее живой проверки.
4. Отправка приглашений: `send_invitations=True` и есть участники →
   `SEND_TO_ALL_AND_SAVE_COPY`; нет участников → `SEND_TO_NONE`
   независимо от флага; `send_invitations=False` → `SEND_TO_NONE`.
5. Все обращения к EWS обернуть в `try/except Exception` →
   `raise translate_ews_error(exc) from exc`.
6. Ответ: `format_event_details(...)` + `"invitations_sent": bool`
   (фактическое значение, а не переданный флаг — при отсутствии
   участников это `False`).
7. Тесты в `tests/test_calendar_write.py` — полный список в спеке,
   раздел «Тестирование», подраздел `create_event`.

### Verification

```bash
uv run pytest tests/test_calendar_write.py -q && uv run pytest -q
```

### Exit criteria

- Все перечисленные в спеке кейсы `create_event` покрыты тестами и
  зелёные.
- Тест доказывает, что при `attendees=[]` в `save()` ушло
  `SEND_TO_NONE` даже при `send_invitations=True`.
- Ни один тест не проверяет поведение через фейк, чей контракт не сверен
  с exchangelib.

---

## Шаг 5. `update_event` и `delete_event` — сервисная логика

**Модель:** strongest
**Зависимости:** шаги 1, 2, 3 (и, желательно, 4 — чтобы не конфликтовать
в `calendar_write.py`)
**Ветка:** `feat/calendar-write-update-delete`

### Context brief

Спек, разделы «`update_event`» и «`delete_event`». Получение item — через
`fetch_one` / `find_by_id_in_calendar` из `calendar_service.py`
(переименованы в шаге 2); повторять логику ChangeKey-fallback **нельзя**.

`item_type` берётся из атрибута `type` item'а (в `format_event_summary`
он уже так и читается: `getattr(item, "type", None)`).

### Task list

1. `update_event(account, config, *, event_id, subject, start, end,
   location, body, attendees, send_invitations, scope) -> dict`.
2. `scope != "occurrence"` → `InvalidArgumentError` («не
   поддерживается»). Проверять **до** обращения к EWS.
3. Получить item (fetch с fallback). Не найден → `ItemNotFoundError`.
4. Тип item: `RecurringMaster` → `InvalidArgumentError` с текстом из
   спека («это правило серии; передайте event_id конкретной встречи из
   list_events»). `Single` / `Occurrence` / `Exception` → продолжаем.
5. Право на правку: сравнить `item.organizer.email_address` с
   `config.ews_email` регистронезависимо. Не совпало →
   `PermissionDeniedError` («встречу может изменить только
   организатор»). Организатор не определяется (`None`) → не блокировать,
   положиться на отказ EWS.
6. Изменение времени (**несимметрично**, это осознанное решение спека):
   - только `start` → длительность сохраняется, `end` двигается на ту же
     дельту (перенос);
   - только `end` → `start` на месте, длительность меняется;
   - оба → как передано;
   - результирующий `end <= start` → `InvalidArgumentError` до EWS.
7. `None` = «не менять». Пустая строка в `subject` → ошибка, не очистка.
8. Сохранение: `item.save(...)` с нужным
   `send_meeting_invitations`. Ошибки `ErrorCannotUpdateObject`,
   `ErrorCalendarCannotUpdateDeletedItem` и родственные → перевести в
   `PermissionDeniedError` (уточнить точные имена классов в
   `exchangelib/errors.py`; те, которых нет в установленной версии, не
   импортировать).
9. `delete_event(account, config, *, event_id, send_cancellations, scope)
   -> dict`: те же проверки типа и организатора. Не организатор → отказ
   (удаление чужой встречи = отклонение приглашения, другая операция).
   Ответ: `{"deleted": True, "event_id", "subject", "cancellations_sent"}`.
10. Тесты — полный список в спеке, разделы `update_event` и
    `delete_event`.

### Verification

```bash
uv run pytest tests/test_calendar_write.py -q && uv run pytest -q
```

### Exit criteria

- Тест доказывает: передан только `start` → длительность неизменна;
  передан только `end` → `start` неизменен.
- Тест доказывает отказ на `RecurringMaster`, на `scope="series"` и на
  чужой встрече (не организатор) — во всех трёх случаях EWS не
  вызывается.
- Весь набор тестов зелёный.

---

## Шаг 6. Регистрация tools в `server.py` под флагом

**Модель:** strongest
**Зависимости:** шаги 4, 5
**Ветка:** `feat/calendar-write-tools`

### Context brief

`server.py` создаёт `mcp = FastMCP("outlook-mcp")` и `_config =
load_config()` на импорте модуля, до ленивой валидации. Каждый tool —
функция под `@mcp.tool()`, ловящая `OutlookMcpError` и возвращающая
`exc.to_dict()`.

**Известный риск, снять его — главная задача шага.** Существующие тесты
зовут tools как обычные функции (`server.list_events(...)`) и **не
проверяют содержимое `tools/list`**. Требование спека «при выключенном
флаге пишущие tools отсутствуют в `tools/list`» проверяется иначе — через
реестр `FastMCP` (в текущей версии `mcp` это, вероятно,
`mcp._tool_manager` / `await mcp.list_tools()`; уточнить по установленной
версии пакета) либо через переимпорт модуля с подменённым конфигом
(`importlib.reload`). **Сначала выяснить, как именно, потом писать
тест** — не подгонять реализацию под догадку.

### Task list

1. Добавить в `server.py` три tool-обёртки: `create_event`,
   `update_event`, `delete_event`. Каждая: валидирует аргументы, зовёт
   сервисную функцию, логирует результат, ловит `OutlookMcpError` →
   `to_dict()`.
2. Регистрацию обернуть в `if _config.allow_write:` — при выключенном
   флаге декораторы `@mcp.tool()` не выполняются, tools отсутствуют в
   реестре.
3. **Docstrings — это интерфейс для LLM**, писать по спеку дословно:
   - участники только email, при неоднозначности имени — сперва
     `resolve_person` и переспросить пользователя;
   - в `update_event` — формулировка в терминах намерения: «чтобы
     перенести встречу, передавайте `start` (длительность сохранится);
     чтобы изменить длительность — `end`; чтобы задать новые границы —
     оба»;
   - очистка полей не поддерживается;
   - занятость не проверяется, для этого `find_free_slots`.
4. Тест в `tests/test_server.py`: при `allow_write=False` три tool
   отсутствуют в реестре MCP, при `True` — присутствуют. Способ —
   выясненный в п. 0 этого шага.
5. Тесты: каждый tool возвращает `to_dict()` при доменной ошибке, а не
   бросает.

### Verification

```bash
uv run pytest -q && uv run ews-mcp-server --help
```

### Exit criteria

- Весь набор тестов зелёный.
- Есть работающий тест на отсутствие tools при `EWS_ALLOW_WRITE=0` —
  именно на реестре, не на вызове функции.
- `--help` по-прежнему завершается, не открывая stdio-сессию.

---

## Шаг 7. Документация

**Модель:** default
**Зависимости:** шаг 6
**Ветка:** `docs/calendar-write`

### Context brief

Сервер перестал быть read-only для календаря. Четыре места объявляют
старое свойство и должны быть исправлены согласованно.

### Task list

1. `README.md`, раздел «Не делать» (строка ~349): заменить «Никакой
   записи в Exchange — только чтение» на «Никакой записи в почту;
   календарь — только через явные пишущие tools (`create_event`,
   `update_event`, `delete_event`), отключаемые `EWS_ALLOW_WRITE=0`».
   Остальные пункты («только EWS/NTLM», «вложения не отдаются») не
   трогать.
2. `README.md`: добавить три tool в список возможностей и описать
   `EWS_ALLOW_WRITE` в разделе переменных окружения.
3. `CLAUDE.md` строка 3: убрать «только для чтения», описать новый модуль
   `calendar_write.py` в разделе «Структура».
4. `CLAUDE.md`, раздел «Важные решения»: добавить решения по записи —
   дефолт отправки приглашений `True` и почему; несимметричное поведение
   `start`/`end`; отказ на `RecurringMaster` и швы под recurrence
   (`scope`, `recurrence`); `EWS_ALLOW_WRITE` не регистрирует tools, а не
   отказывает; all-day — флаг, а не `00:00–23:59`.
5. `server.py`, текст `--help` (строка ~283): убрать «read-only»,
   упомянуть `EWS_ALLOW_WRITE`.
6. `install/README.md`: описать `EWS_ALLOW_WRITE` как способ получить
   read-only сервер. **Сами install-скрипты не менять** — переменная при
   установке не спрашивается.

### Verification

```bash
grep -rn -i "read-only\|только чтени\|Никакой записи" README.md CLAUDE.md src/ install/
uv run pytest -q
```

### Exit criteria

- `grep` не находит утверждений, что сервер read-only, кроме
  относящихся к почте.
- Тесты зелёные.

---

## Шаг 8. Ручная проверка на живом Exchange

**Модель:** выполняет пользователь (нужен VPN и реальный ящик)
**Зависимости:** шаг 7
**Ветка:** та же, что у шага 7 (правки по итогам)

### Context brief

История проекта прямая: `WorkingPeriod.weekdays`, путь `sender`, наивное
время от `GetUserAvailability`, `list_events` с сериями — всё прошло
юнит-тесты и сломалось только вживую. Для записи цена ошибки выше:
страдает чужой календарь.

### Task list

Порядок — от безопасного к рискованному. Шаги 1–4, 6, 7 — на встречах
**без участников**.

1. `create_event` без участников → в Outlook проверить время, дату, тему.
2. `update_event` переносом (только `start`) и растягиванием (только
   `end`) → проверить, что длительность ведёт себя как задумано.
3. `create_event` с `all_day=True` на 2 дня → это плашка, а не полоса
   через весь день; **второй день включён**. Здесь проверяется
   допущение, помеченное комментарием в шаге 4.
4. `delete_event` → встреча исчезла.
5. `create_event` **с одним участником** (свой другой адрес или
   заранее предупреждённый коллега) → приглашение дошло; затем
   `update_event` (обновление дошло) и `delete_event` (отмена дошла).
6. `update_event` на экземпляре серии → изменился один экземпляр, серия
   цела.
7. `update_event` на чужой встрече → внятный отказ, не сырой EWS-код.

### Exit criteria

- Все 7 проверок пройдены, найденные расхождения исправлены.
- **Каждое расхождение зафиксировано в `CLAUDE.md`** — как для
  остальных подсистем.
- В `conftest.py` поправлены фейки, если живое поведение разошлось с
  предположениями (правило проекта №1).

---

## Changelog

- 2026-08-15 — план создан.
