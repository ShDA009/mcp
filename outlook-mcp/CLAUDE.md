# outlook-mcp

MCP-сервер только для чтения календаря и почты из on-prem Exchange по EWS
(SOAP/NTLM), для потребителя Cline. Полный план — [task-ews-mcp-v3.md](task-ews-mcp-v3.md).

## Статус

- **Фаза 1 (готово)**: каркас MCP-сервера (stdio, FastMCP), EWS-клиент
  (`exchangelib`, NTLM), tool `list_events`, Dockerfile, юнит-тесты.
- **Фаза 2 (готово)**: `get_event(event_id)` с полными деталями, `list_events`
  поддерживает диапазон дат (`target_date`/`end_date`).
- **Фаза 3 (готово)**: почта — `list_emails`, `get_email`, `search_emails`.
- **Фаза 4 (готово)**: полировка ошибок (`InvalidArgumentError`,
  `ConfigurationError`), README, опциональный `find_free_slots`. Все фазы
  плана завершены.

## Структура

- `src/outlook_mcp/config.py` — переменные окружения (`EWS_URL`,
  `EWS_USERNAME`, `EWS_EMAIL`, `EWS_PASSWORD`, таймзона, лимиты). Валидация
  ленивая — происходит при первом вызове tool, не при старте сервера.
  `EWS_USERNAME` — логин для NTLM (без домена/@suffix), `EWS_EMAIL` — полный
  SMTP-адрес ящика для `primary_smtp_address` в exchangelib. Это два разных
  значения: NTLM-аутентификация не требует email, а `exchangelib.Account`
  требует email, чтобы понять, какой ящик открывать. Проект открытый —
  домен намеренно не зашит в код, только через явную переменную.
  **Fallback на файл `.env`**: если переменной нет в `os.environ`, `Config`
  дочитывает её из `_ENV_FILE` (`~/.config/outlook-mcp/.env` на macOS/Linux,
  `%USERPROFILE%\.outlook-mcp\.env` на Windows — путь зависит от
  `platform.system()`, должен совпадать с тем, что пишет `install/setup.sh` /
  `setup.ps1`). Так `cline_mcp_settings.json` не должен содержать секцию
  `env` вовсе — install-скрипты прописывают только `command`/`args`, креды
  не дублируются в JSON. `os.environ` всегда приоритетнее файла. Тесты
  (`tests/test_config.py`) monkeypatch'ят `_ENV_FILE` на несуществующий путь
  через autouse-фикстуру `no_env_file`, чтобы не зависеть от реального `.env`
  на машине разработчика.
- `src/outlook_mcp/ews_client.py` — подключение к EWS (`exchangelib.Account`,
  NTLM), маппинг исключений `exchangelib` → доменные ошибки.
- `src/outlook_mcp/errors.py` — типизированные ошибки
  (`ConnectionUnavailableError`, `AuthenticationError`, `ThrottlingError`,
  `ItemNotFoundError`), каждая сериализуется в структурированный JSON
  через `to_dict()` — так они попадают в ответ tool вместо стектрейса.
- `src/outlook_mcp/formatting.py` — вся логика парсинга EWS-объектов в JSON:
  конвертация времени в `Europe/Moscow`, статусы участников, HTML→text,
  `limit`/`has_more`. Это модуль с наибольшим покрытием тестами.
- `src/outlook_mcp/calendar_service.py` — запросы к `account.calendar`:
  `list_events_for_range` (диапазон дат), `get_event_by_id` (одиночный item
  по `event_id`, с fallback-разрешением устаревшего ChangeKey) и
  `find_free_slots` (через `exchangelib.services.GetUserAvailability`).
- `src/outlook_mcp/mail_service.py` — запросы к почтовым папкам
  (`inbox`/`sent`/`drafts`/`junk`/`deleted` через `_FOLDER_ATTRS`):
  `list_emails`, `search_emails` (через `exchangelib.restriction.Q` по
  теме/отправителю/телу), `get_email_by_id` (тот же паттерн
  fetch-с-changekey + fallback-скан, что и в `calendar_service.py`).
- `src/outlook_mcp/server.py` — регистрация MCP tools (`FastMCP`), точка входа.
  `main()` также обрабатывает `--help`/`-h`: печатает справку и завершается
  **не открывая stdio-сессию** (иначе процесс завис бы в ожидании ввода) —
  это лёгкая самопроверка для установочных скриптов.
- `install/` — установочные скрипты для сотрудников (`setup.sh` для
  macOS arm64 + Linux, `setup.ps1` для Windows, `install/README.md`).
  Запуск сервера через `uvx --from git+<repo>#subdirectory=outlook-mcp
  ews-mcp-server` (не Docker). Скрипты идемпотентны: находят/ставят `uv`,
  пишут `.env` (chmod 600), обновляют секцию `outlook-mcp` в
  `cline_mcp_settings.json` на месте. Все 4 env-переменные (`EWS_URL`,
  `EWS_USERNAME`, `EWS_EMAIL`, `EWS_PASSWORD`) обязательны и спрашиваются у
  сотрудника — домен в код не зашивается. Entry point `ews-mcp-server`
  объявлен в `[project.scripts]` (`pyproject.toml`).

## Важные решения

- **event_id** — это `"{item_id}:{changekey}"` (см. `encode_item_id` /
  `decode_item_id` в `formatting.py`). ChangeKey нужен для повторного чтения
  item в `get_event` — при изменении item старый ChangeKey может быть невалиден.
- **get_event и устаревший ChangeKey**: EWS требует и `id`, и `changekey`
  вместе (`ItemId` — обязательная пара полей в протоколе), поэтому
  `account.fetch(ids=[(id, changekey)])` — единственный способ получить item
  по ID напрямую. Если changekey устарел (`ErrorInvalidChangeKey`/
  `ErrorItemNotFound`/`ErrorInvalidIdMalformed`) или вовсе не был передан,
  `get_event_by_id` в `calendar_service.py` делает fallback: сканирует
  календарь в окне ±180 дней от сегодня (`_ID_RESOLUTION_WINDOW_DAYS`) и
  ищет item с совпадающим `id`. exchangelib не поддерживает фильтрацию
  `.filter(id=...)` — `id`/`changekey` не обычные поля item, поэтому только
  полный перебор внутри окна.
- **Ошибки** не пробрасываются как исключения через MCP-протокол — каждый
  tool ловит `OutlookMcpError` и возвращает `exc.to_dict()` как обычный
  результат (см. `list_events` в `server.py`). LLM-клиент должен получать
  понятный JSON с `error`/`message`, а не падение вызова.
- **exchangelib 5.6.0**: throttling — это `RateLimitError` (требует
  позиционный `wait`), не `ErrorThrottled` (такого класса в этой версии нет).
  Если апгрейдить `exchangelib`, перепроверить `tests/test_errors.py` —
  список исключений там жёстко завязан на версию.
- Валидация `Config` (`validate()`) не запускается в конструкторе — иначе
  контейнер не смог бы стартовать и отдать `tools/list` без реальных кредов
  (это часть автономного критерия готовности, проверяется без VPN).
- **Содержимое вложений никогда не отдаётся** — `format_attachment_metadata`
  в `formatting.py` берёт только `name`/`content_type`/`size`, не трогает
  `attachment.content`. Это жёсткое ограничение из плана, не забыть при
  добавлении новых полей к письмам.
- **Папки почты** — фиксированный набор алиасов в `_FOLDER_ATTRS`
  (`mail_service.py`): `inbox`/`sent`/`drafts`/`junk`/`deleted` →
  соответствующие атрибуты `Account`. Произвольные имена папок (кастомные
  подпапки) не поддерживаются в Фазе 3 — `_resolve_folder` кидает
  `InvalidArgumentError` для неизвестных имён.
- **find_free_slots и рабочие часы**: EWS хранит рабочие часы пользователя
  как часть протокола `GetUserAvailability`. **Не вызывать `GetUserAvailability`
  напрямую с самодельным `timezone=`** — параметр `timezone` этого сервиса
  требует специфичный SOAP `TimeZone`-элемент (bias/DST-правила), собрать
  который вручную непрактично; `timezone=None` реально ловится EWS как
  "Unsupported type None on timezone" (найдено при ручной проверке на живом
  Exchange). Правильный путь — `account.protocol.get_free_busy_info(accounts=
  [(account, "Organizer", False)], start=..., end=...)`: это готовая обёртка
  в exchangelib (`protocol.py`), которая сама строит `TimeZone` через
  `TimeZone.from_server_timezone(...)`. Единственное требование — `start`/`end`
  datetime должны иметь `tzinfo`, которое умеет `get_timezones()`, то есть
  `EWSTimeZone` (наследник `zoneinfo.ZoneInfo` из exchangelib), не голый
  `zoneinfo.ZoneInfo` — конвертировать через `EWSTimeZone.from_zoneinfo(tz)`.
  Ответ — `FreeBusyView` с `working_hours` (список `WorkingPeriod` с
  `weekdays`/`start`/`end`, где `weekdays` — строки вида `"Monday"`) и
  `calendar_events` (занятые интервалы). `find_free_slots` в
  `calendar_service.py` берёт рабочий период для дня недели искомой даты,
  вычитает занятые интервалы и нарезает оставшиеся окна на слоты нужной
  длительности. Если для дня недели не задан `WorkingPeriod` (выходной по
  настройкам Exchange) — возвращает `{"slots": [], "has_more": false}`, не ошибку.
- **list_events/list_emails/search_emails и лимиты**: изначальная реализация
  делала `list(qs)` — материализовывала **весь** результат EWS-запроса на
  клиенте, потом резала по `limit`. На живом Exchange с непустой папкой это
  приводило к таймауту (EWS пытался вернуть все письма из Inbox). Исправлено:
  `list(qs[:effective_limit + 1])` — exchangelib транслирует slice в
  `max_items`/`page_size` на `QuerySet` (см. `exchangelib/queryset.py:
  __getitem__`), что ограничивает сам SOAP-запрос (`FindItem` с
  `IndexedPageView`), а не просто обрезает результат после получения.
  `+1` нужен, чтобы отличить "ровно limit результатов" от "результатов
  больше, чем limit" (`has_more`). Это правило актуально для любого нового
  tool, который листает папку/календарь — никогда не делать `list(qs)` без
  среза, если объём результата не гарантированно мал.
- **list_events и повторяющиеся встречи (`view()` vs `filter()`)**:
  `account.calendar.filter(...)` возвращает только `RecurringMaster` — одну
  запись серии с её *исходными* start/end, поэтому регулярные встречи либо
  показывались с датой первого проведения, либо вовсе выпадали из окна
  (найдено на живом календаре: дейли-встреча была видна сегодня, но
  пропадала на завтра). Исправлено переходом на
  `account.calendar.view(start=start_dt, end=end_dt, max_items=...)` —
  exchangelib реализует через него EWS `CalendarView`, который разворачивает
  серию в отдельные `Occurrence`-items внутри запрошенного диапазона.
  Компромиссы: (1) `max_items` — прямая замена правилу слайса выше, `view()`
  сам ограничивает `FindItem`, слайсить `qs[:n]` не нужно и не имеет смысла;
  (2) EWS запрещает комбинировать `CalendarView` с restrictions, поэтому
  `.order_by("-start")` пришлось убрать — `list_events` теперь отдаёт
  события в хронологическом порядке (по возрастанию `start`), а не в
  обратном. `_find_by_id_in_calendar` (fallback-скан по ChangeKey) осознанно
  остался на `filter()` — там как раз нужен master, чтобы резолвить id.
  `format_event_summary` теперь всегда отдаёт `is_recurring` и `item_type`
  (`Single`/`Occurrence`/`Exception`/`RecurringMaster`), а не только
  `format_event_details` — иначе клиент не может отличить экземпляр серии
  от одиночной встречи прямо в списке.
- **search_emails и путь `sender`**: `Q(sender__email_address__contains=...)`
  ломается с `InvalidField: Unknown field path 'sender__email_address'`
  (найдено при ручной проверке на живом Exchange). Причина: `sender` в
  exchangelib — `MailboxField` (комплексное поле типа `Mailbox`), а не
  `MultiFieldIndexedElement` — для `MailboxField` **нельзя** указывать
  субполе в field path вообще (`FieldPath.from_string` явно кидает
  `ValueError: must not specify label or subfield` на путь с `__`).
  Правильный путь — просто `Q(sender__contains=query)`: exchangelib сам
  строит EWS `Contains`-restriction на значимое подполе `Mailbox`
  (`is_searchable=True` подтверждено на голом `sender`). Урок: перед тем как
  писать `field__subfield__lookup` для комплексного поля, проверять тип
  поля (`Message.get_field_by_fieldname(name)`) — `MailboxField`/
  `EWSElementField` в общем случае не поддерживают путь к субполю таким
  образом, в отличие от `MultiFieldIndexedElement` (email-адреса контакта
  и т.п.), где путь `field__label` обязателен.
- **ConfigError теперь наследует OutlookMcpError** (`ConfigurationError` в
  `errors.py`, `config.py` использует alias `ConfigError = ConfigurationError`
  для обратной совместимости с существующими импортами/тестами). До этого
  отсутствие обязательных env-переменных приводило к необработанному
  исключению вместо структурированного JSON-ответа — нарушало критерий
  готовности "все tools отдают корректную структурированную ошибку".
- **Валидация аргументов tools** — `server.py` оборачивает
  `date.fromisoformat`/`limit`/`duration_min` через `_parse_date`/
  `_validate_limit`, которые кидают `InvalidArgumentError` вместо того,
  чтобы дать `ValueError` вылететь из tool необработанным.

## Тесты

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/ --cov=outlook_mcp --cov-report=term-missing
```

Все тесты — на фикстурах/моках (`tests/conftest.py`), без сети и без
реального `exchangelib`-клиента. Текущее покрытие ~80%, модули
парсинга/форматирования 88-100%.

## Проверка EWS endpoint без Python

Перед реализацией/после смены кредов — быстрая curl-проверка (SOAP
`GetFolder` на Inbox, `--ntlm`):

```bash
curl -k --ntlm -u 'EWS_USERNAME:EWS_PASSWORD' \
  -H 'Content-Type: text/xml; charset=utf-8' \
  -X POST 'https://<host>/EWS/Exchange.asmx' \
  --data-binary @- <<'EOF'
<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types"
               xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages">
  <soap:Header><t:RequestServerVersion Version="Exchange2013_SP1" /></soap:Header>
  <soap:Body>
    <m:GetFolder>
      <m:FolderShape><t:BaseShape>IdOnly</t:BaseShape></m:FolderShape>
      <m:FolderIds><t:DistinguishedFolderId Id="inbox" /></m:FolderIds>
    </m:GetFolder>
  </soap:Body>
</soap:Envelope>
EOF
```

Успех — `ResponseClass="Success"` в теле ответа. Требует VPN/корп. сеть.

## Docker

```bash
docker build -t outlook-mcp .
docker run -i --rm --env-file .env outlook-mcp
```

Cline подключается через stdio (`docker run -i`). В `cline_mcp_settings.json`
использовать `--env-file` с абсолютным путём к `.env`, а не `-e` в `args`
(креды из `-e` попадают в системный промпт AI-модели). См. [README.md](README.md)
для готового фрагмента.

## Не делать (жёсткие ограничения из плана)

- Никакой записи в Exchange (только чтение).
- Никакого OAuth/Graph API — только EWS/NTLM.
- Не отдавать содержимое вложений, только метаданные.
- Не логировать тела писем/встреч и креды.
