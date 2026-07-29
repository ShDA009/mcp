# outlook-mcp

MCP-сервер только для чтения календаря и почты из on-prem Exchange по EWS
(SOAP/NTLM). Транспорт — stdio. Потребитель — Cline. Запускается через
`uvx` (рекомендуется) или в Docker.

## Установка для сотрудников (uvx, без Docker)

Готовые установочные скрипты в [install/](install/) — под Windows, macOS
(Apple Silicon) и Linux. Скрипт находит/ставит `uv`, спрашивает креды,
сохраняет `.env` и прописывает сервер в Cline идемпотентно. Инструкция —
[install/README.md](install/README.md).

Запуск сервера под капотом:

```bash
uvx --from git+https://github.com/ShDA009/mcp.git#subdirectory=outlook-mcp ews-mcp-server
```

`ews-mcp-server` — консольный entry point (см. `[project.scripts]` в
[pyproject.toml](pyproject.toml)). `--help` печатает справку и завершается
без запуска stdio-сессии (используется скриптами для проверки установки).

## Сборка и запуск (Docker, альтернатива)

При установке через `install/` (раздел выше) весь этот раздел не нужен —
скрипт сам находит/ставит `uv`, пишет `.env` в `~/.config/outlook-mcp/.env` и
прописывает Cline. Ниже — только для тех, кто предпочитает Docker вместо uvx.

```bash
docker build -t outlook-mcp .
docker run -i --rm --env-file .env outlook-mcp
```

Конфигурация (`.env`) — скопировать [.env.example](.env.example) и заполнить:

```bash
EWS_URL=https://mail.example.com/EWS/Exchange.asmx
# EWS_USERNAME - логин для NTLM, без домена и без @suffix (напр. "ivanov")
EWS_USERNAME=ivanov
# EWS_EMAIL - полный SMTP-адрес ящика, нужен exchangelib отдельно от NTLM-логина
EWS_EMAIL=ivanov@example.com
EWS_PASSWORD=changeme

# опционально:
# OUTLOOK_MCP_TIMEZONE=Europe/Moscow
# OUTLOOK_MCP_DEFAULT_LIMIT=50
# OUTLOOK_MCP_MAX_LIMIT=200
```

`.env` не должен попадать в репозиторий и не вшивается в образ — только
через `--env-file` при запуске контейнера.

Фрагмент `cline_mcp_settings.json` для Docker-варианта:

```json
"outlook-mcp": {
  "disabled": false,
  "timeout": 60,
  "type": "stdio",
  "command": "docker",
  "args": [
    "run",
    "-i",
    "--rm",
    "--env-file",
    "/absolute/path/to/outlook-mcp/.env",
    "outlook-mcp:latest"
  ]
}
```

Где `/absolute/path/to/outlook-mcp/.env` — абсолютный путь к вашему
локальному файлу `.env`. Креды передаются только через `--env-file`,
никогда через `-e` в `args` (иначе они попадут в системный промпт
AI-модели).

## Tools

| Tool | Параметры | Описание |
|---|---|---|
| `list_events` | `target_date?: str (YYYY-MM-DD)`, `end_date?: str (YYYY-MM-DD)` | Встречи за день или диапазон дат (по умолчанию — сегодня), время в `Europe/Moscow` |
| `get_event` | `event_id: str` | Полные детали встречи: тело, участники, локация, признак повторяемости |
| `find_free_slots` | `target_date: str (YYYY-MM-DD)`, `duration_min: int`, `emails?: list[str]`, `include_self?: bool = true`, `debug?: bool = false` | Свободные окна нужной длительности в рабочие часы дня (рабочие часы читаются из настроек Exchange через `GetUserAvailability`). Если передан `emails`, ищутся окна, свободные одновременно у указанных коллег (и у своего ящика, если `include_self=true`); рабочие часы всегда берутся из своего ящика. Встречи «под вопросом» (`Tentative`) отдельно перечислены в `tentative_slots`. Пустой `slots` всегда сопровождается полем `reason`, объясняющим причину; `debug=true` добавляет подробную диагностику free/busy для отладки |
| `resolve_person` | `query: str` | Поиск email по (частичному) имени в адресной книге Exchange (EWS `ResolveNames`). Возвращает список кандидатов `{name, email}`; при отсутствии совпадений — пустой список, не ошибка. Используется перед `find_free_slots`, когда известно только имя, а не email |
| `list_emails` | `folder="Inbox"`, `start_date?`, `end_date?`, `unread_only=false`, `limit?` | Письма в папке (`Inbox`/`Sent`/`Drafts`/`Junk`/`Deleted`), с фильтром по дате и признаку прочтения |
| `get_email` | `email_id: str` | Полные детали письма: тело (plain text), метаданные вложений — **без содержимого вложений** |
| `search_emails` | `query: str`, `folder="Inbox"`, `start_date?`, `end_date?`, `limit?` | Поиск по теме/отправителю/телу письма |

Пример ответа `list_events`:

```json
{
  "events": [
    {
      "event_id": "AAA:CCC",
      "subject": "Sync",
      "start": "2026-07-15T13:00:00+03:00",
      "end": "2026-07-15T14:00:00+03:00",
      "organizer": {"name": "Boss", "email": "boss@example.com"},
      "attendees": [{"name": "Alice", "email": "alice@example.com", "response_status": "accepted"}],
      "response_status": "accepted",
      "location": "Room 1"
    }
  ],
  "has_more": false
}
```

Пример ответа `get_event`:

```json
{
  "event_id": "AAA:CCC",
  "subject": "Sync",
  "start": "2026-07-15T13:00:00+03:00",
  "end": "2026-07-15T14:00:00+03:00",
  "organizer": {"name": "Boss", "email": "boss@example.com"},
  "attendees": [{"name": "Alice", "email": "alice@example.com", "response_status": "accepted"}],
  "response_status": "accepted",
  "location": "Room 1",
  "body": "Agenda: ...",
  "is_recurring": false
}
```

Пример ответа `find_free_slots`:

```json
{
  "slots": [
    {"start": "2026-07-15T09:00:00+03:00", "end": "2026-07-15T10:00:00+03:00"},
    {"start": "2026-07-15T11:00:00+03:00", "end": "2026-07-15T12:00:00+03:00"}
  ],
  "tentative_slots": [
    {"start": "2026-07-15T10:00:00+03:00", "end": "2026-07-15T11:00:00+03:00"}
  ],
  "has_more": false,
  "unavailable": [],
  "reason": "ok"
}
```

**Поле `reason`** присутствует в ответе всегда и объясняет пустой `slots`,
не заставляя гадать: `ok` (слоты найдены), `no_working_hours_for_weekday`
(выходной/нет рабочих часов на этот день), `fully_busy` (весь рабочий день
занят), `no_window_fits_duration` (свободное время есть, но ни одно окно не
вмещает `duration_min` — попробуйте меньшую длительность), `only_tentative`
(свободных нет, но есть `tentative_slots`), `all_participants_unavailable`
(при `include_self=false` free/busy не получилось прочитать ни у одного
участника — значит, календарь фактически не проверялся, и «весь день
свободен» было бы ложным результатом).

Если для дня не заданы рабочие часы (например, выходной по настройкам
Exchange) — `slots`/`tentative_slots` пустые массивы, это не ошибка.

**Классификация занятости** (поле `busy_type` в EWS free/busy): `Busy` и
`OOF` (не на месте) блокируют слот. `Tentative` («под вопросом») тоже
блокирует его в `slots`, но такой слот отдельно попадает в
`tentative_slots` — время формально есть, но у кого-то встреча под
вопросом, стоит предупредить об этом. `Free` и `WorkingElsewhere` не
блокируют. Если статус не указан — считается `Busy` (безопасный дефолт).
`slots` и `tentative_slots` никогда не пересекаются.

С параметром `emails` free/busy недоступных участников (нет прав, ящик не
найден) не роняет вызов — такие адреса попадают в `unavailable`, а слоты
считаются по остальным:

```json
{
  "slots": [
    {"start": "2026-07-15T09:00:00+03:00", "end": "2026-07-15T09:30:00+03:00"}
  ],
  "tentative_slots": [],
  "has_more": false,
  "unavailable": [
    {"email": "unknown@example.com", "reason": "Requested item was not found"}
  ],
  "reason": "ok"
}
```

По умолчанию свой ящик всегда участвует в пересечении занятости
(`include_self=true`, «когда можем встретиться МЫ»). Чтобы посмотреть,
когда свободны только указанные коллеги, независимо от своего календаря —
`include_self=false` (обязательно вместе с непустым `emails`; без него —
`{"error": "invalid_argument"}`, так как проверять было бы нечей календарь).
Рабочие часы при этом всё равно берутся из своего ящика.

**Диагностика (`debug=true`)**: если результат неожиданный (например,
`reason: "fully_busy"`, хотя в самом Outlook видны промежутки), повторите
вызов с `debug=true` — в ответе появится поле `diagnostics` с разбором по
каждому участнику: тип ответа EWS (`view_type`), количество и содержимое
`calendar_events` в локальном времени с их `busy_type`, признак наличия и
длина `merged`-представления (грубая шкала занятости с шагом 30 минут,
которую EWS может отдавать вместо детальных событий — например, при
пониженном уровне прав на просмотр чужого календаря), гистограмма
`busy_type`, и посчитанные занятые/свободные интервалы до и после отсечения
по рабочему окну. Оставляйте `debug=false` в обычных сценариях — поле
существенно увеличивает объём ответа.

Пример ответа `resolve_person` (запрос по (частичному) имени, не email —
`find_free_slots` принимает только email, поэтому сначала `resolve_person`,
затем найденный адрес передаётся в `emails`):

```json
{
  "candidates": [
    {"name": "Ivanov Ivan Petrovich", "email": "i.ivanov@example.com"}
  ]
}
```

Если совпадений нет — `{"candidates": []}`, это не ошибка. Если совпадений
несколько (частые ФИО) — в списке будет несколько кандидатов, выбор между
ними остаётся за вызывающим (LLM должен уточнить у пользователя, если
неоднозначно).

Пример ответа `list_emails`:

```json
{
  "emails": [
    {
      "email_id": "EEE:FFF",
      "subject": "Weekly report",
      "sender": {"name": "Alice", "email": "alice@example.com"},
      "date": "2026-07-15T12:00:00+03:00",
      "is_read": true,
      "has_attachments": true
    }
  ],
  "has_more": false
}
```

Пример ответа `get_email`:

```json
{
  "email_id": "EEE:FFF",
  "subject": "Weekly report",
  "sender": {"name": "Alice", "email": "alice@example.com"},
  "date": "2026-07-15T12:00:00+03:00",
  "is_read": true,
  "has_attachments": true,
  "body": "See attached report.",
  "attachments": [
    {"name": "report.pdf", "content_type": "application/pdf", "size": 1024}
  ]
}
```

Ошибки возвращаются как структурированный JSON, не как стектрейс:

```json
{"error": "connection_unavailable", "message": "Could not reach EWS endpoint (check VPN/network)"}
```

Коды: `connection_unavailable`, `authentication_error`, `throttling_error`,
`item_not_found`, `invalid_argument` (неверный формат даты, неизвестное имя
папки, неположительный `limit`/`duration_min`), `configuration_error`
(не заданы обязательные переменные окружения).

## Безопасность кред

- `.env` — только локально, в `.gitignore`. В репозитории — только `.env.example`.
  При установке через `install/` он лежит в `~/.config/outlook-mcp/.env`
  (Windows: `%USERPROFILE%\.outlook-mcp\.env`), при Docker-варианте — рядом с
  проектом, путь к нему передаётся через `--env-file`.
- На боевой машине предпочтительно брать креды из системного keychain / KeePass
  (`keeenv`), а не хранить plaintext в `.env`. Сама keychain-интеграция пока
  не реализована — это только рекомендация.
- В логах — только метаданные (количество элементов, коды ошибок, тайминги),
  без тел писем/встреч и без кредов.

---

Разделы ниже — для разработки и отладки сервера, не нужны для установки и
обычного использования.

## Тесты

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/ --cov=outlook_mcp --cov-report=term-missing
```

Все тесты работают на фикстурах/моках, без сети и без живого Exchange.

## Проверка EWS вручную (нужен VPN)

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

Успех — `ResponseClass="Success"` в теле ответа.

## Ручная проверка tools (нужен VPN)

Автономно (без VPN) проверяется только сборка образа, регистрация tools по
stdio и структурированные ошибки при недоступном EWS — это покрыто тестами
и CI. Реальные данные с боевого EWS проверяются вручную:

1. Подключить сервер в Cline с реальными `EWS_URL`/`EWS_USERNAME`/`EWS_EMAIL`/`EWS_PASSWORD`.
2. Убедиться, что VPN/корп. сеть доступны (см. curl-проверку выше).
3. Вызвать по очереди `list_events`, `get_event` (с `event_id` из ответа
   `list_events`), `list_emails`, `get_email` (с `email_id` из ответа
   `list_emails`) и убедиться, что возвращаются реальные данные, а не ошибка.
4. При желании проверить `find_free_slots` и `search_emails` аналогично,
   включая `find_free_slots` с параметром `emails` (реальный адрес коллеги
   и заведомо несуществующий адрес — второй должен попасть в `unavailable`,
   а не сломать вызов).
5. Проверить `resolve_person` с реальным (частичным) именем коллеги из ГАБ
   и с заведомо несуществующим именем — во втором случае должен вернуться
   `{"candidates": []}`, а не ошибка.
6. Проверить `find_free_slots` с `include_self=false` и непустым `emails` —
   слоты должны отражать только занятость коллег, а не свой календарь; без
   `emails` тот же вызов должен вернуть `{"error": "invalid_argument"}`.
   Отдельно проверить день со встречей со статусом «под вопросом» —
   она должна попасть в `tentative_slots`, а не в `slots`.
7. Если `find_free_slots` для чужого email возвращает `reason: "fully_busy"`
   там, где в Outlook у этого человека видны промежутки между встречами —
   повторить вызов с `debug=true` и разобрать `diagnostics.views[].
   view_type`/`merged_present`/`calendar_events_count`/`raw_events`, чтобы
   понять, отдаёт ли EWS для этого ящика только грубое `merged`-представление
   вместо детальных событий (частая причина — пониженный уровень прав
   free/busy на чужой ящик).

## Не делать

- Никакой записи в Exchange — только чтение.
- Только EWS/NTLM, без OAuth/Graph API.
- Содержимое вложений не отдаётся, только метаданные.
