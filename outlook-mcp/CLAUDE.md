# outlook-mcp

MCP-сервер только для чтения календаря и почты из on-prem Exchange по EWS
(SOAP/NTLM), для потребителя Cline. Реализация завершена: календарь
(`list_events`, `get_event`, `find_free_slots`), адресная книга
(`resolve_person`), почта (`list_emails`, `get_email`, `search_emails`),
структурированная обработка ошибок.

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
- `src/outlook_mcp/directory_service.py` — `resolve_person`: поиск email по
  (частичному) имени через `account.protocol.resolve_names` (EWS
  `ResolveNames`, поиск по ГАБ). "Ничего не найдено" — штатный исход
  (`{"candidates": []}`), не ошибка: EWS сам возвращает
  `ErrorNameResolutionNoResults` как перехваченное значение, а не
  пробрасывает его (в отличие от ошибок `GetUserAvailability`, см. ниже).
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

- **build_account форсирует version guessing** (`ews_client.py`): при
  `autodiscover=False` `exchangelib.Account.__init__` **не** определяет
  версию Exchange-сервера — `config.version` остаётся `None`, пока что-то не
  обратится к `account.version` (property в `account.py`, которая при первом
  чтении вызывает `Version.guess(...)` и кеширует результат). Большинство
  операций (`account.calendar`, `account.fetch`, ...) где-то по цепочке
  трогают `account.version` сами, поэтому обычно это незаметно. Но сервисы,
  вызываемые напрямую через `account.protocol.*`
  (`get_free_busy_info`/`GetUserAvailability` в `calendar_service.py`,
  `resolve_names`/`ResolveNames` в `directory_service.py`) этот путь
  полностью обходят: если такой tool оказывается **первым** EWS-вызовом в
  сессии, `EWSService._version_hint` (`exchangelib/services/common.py:230`,
  читает `self.protocol.config.version` напрямую, в обход property —
  комментарий там же объясняет, что это сделано для самого guessing) падает
  с `AttributeError: 'NoneType' object has no attribute 'api_version'`.
  Воспроизведено вживую: `resolve_person`, вызванный первым, падал именно
  так. Исправлено — `build_account` сразу после создания `Account` читает
  `account.version` (побочный эффект — заполнение `config.version`), так что
  guessing происходит один раз при подключении, независимо от того, какой
  tool вызовут первым. При добавлении нового tool, который ходит в
  `account.protocol.*` напрямую (а не через `account.calendar`/`account.root`
  и т.п.), можно рассчитывать на этот инвариант — отдельно форсировать
  guessing в каждом сервисе не нужно.
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
  настройкам Exchange) — возвращает `{"slots": [], "tentative_slots": [],
  "has_more": false, "unavailable": []}`, не ошибку.
- **find_free_slots и `busy_type`**: изначальная реализация игнорировала
  `busy_type` каждого `CalendarEvent` в `FreeBusyView.calendar_events` и
  считала занятым любой интервал — из-за этого встреча со статусом
  `Free`/`WorkingElsewhere` ошибочно вырезала слот. exchangelib:
  `CalendarEvent.busy_type` — `FreeBusyStatusField(is_required=True,
  default="Busy")` (`properties.py:958`), допустимые значения `Free |
  Tentative | Busy | OOF | NoData | WorkingElsewhere` (`fields.py:934`).
  Классификация в `_busy_intervals_within`/`_HARD_BUSY_TYPES`
  (`calendar_service.py`): `Busy`/`OOF` блокируют слот безусловно; `Free`,
  `WorkingElsewhere`, `NoData` не блокируют; отсутствующий/пустой
  `busy_type` трактуется как `Busy` — безопасный дефолт, совпадающий с
  дефолтом самого EWS-поля, никогда не предлагаем слот, который может быть
  занят.
- **find_free_slots и `tentative_slots`**: `Tentative` («под вопросом»)
  блокирует слот в `slots`, но такой слот отдельно попадает в
  `tentative_slots` — реальный кандидат, но с оговоркой. **Реализовано
  одной сеткой, не двумя проходами** — это принципиально, не
  "упрощать" обратно. Причина: `_iter_slot_starts` начинает отсчёт с
  `slot_start` каждого свободного окна; если считать слоты дважды (без
  tentative и с ним) и взять разность списков, tentative-интервал,
  заканчивающийся не на границе слота (например, 10:00–10:30 при
  часовых слотах), сдвинет всю последующую сетку окна — и разность ложно
  пометит честно свободные более поздние слоты (11:00, 12:00…) как
  tentative, хотя они просто оказались на другой сетке.
  Правильный алгоритм: сетка строится один раз, только по
  `_HARD_BUSY_TYPES` (`Busy`+`OOF`), затем каждый сгенерированный слот
  проверяется на пересечение с объединёнными tentative-интервалами через
  `_overlaps_any` (строгие неравенства — интервал, лишь касающийся
  границы слота, не считается пересечением). `slots` и `tentative_slots`
  дизъюнктны структурно: каждый слот сетки попадает ровно в один список.
- **find_free_slots для нескольких участников** (`emails` в `server.py`/
  `calendar_service.py`): чужой ящик передаётся в `accounts=` **обычной
  строкой** (SMTP-адрес), не `Mailbox` — `protocol.py` строит
  `MailboxData.email` как `account.primary_smtp_address if
  isinstance(account, Account) else account`, т.е. `str` — поддерживаемый
  вариант для ящика, которым не владеем.
  **Один запрос `get_free_busy_info` на каждого участника, не один батч на
  всех** — это принципиально, не "оптимизировать" обратно. exchangelib
  отдаёт исключение как *значение* в ответе (не роняя вызов) только для
  ошибок из `EWSService.ERRORS_TO_CATCH_IN_RESPONSE`
  (`services/common.py:76`, там есть `ErrorMailRecipientNotFound`), а всё
  остальное — `ErrorNoFreeBusyAccess`, `ErrorProxyRequestNotAllowed`,
  `ErrorMailboxMoved`, `ErrorNonExistentMailbox` — **пробрасывается** и в
  батче убило бы результат сразу по всем участникам. Раз по требованию
  недоступный участник должен быть пропущен, а не ронять вызов — только
  отдельный запрос на каждый email с собственным `try/except`
  (`_collect_participant_views` в `calendar_service.py`) даёт нужную
  изоляцию. Оба режима отказа (брошенное исключение и `Exception`,
  вернувшийся как значение результата) обрабатываются одинаково.
  Рабочие часы (`WorkingPeriod`) всегда берутся из **своего** `own_view`,
  чужие рабочие часы не пересекаются — так решено с пользователем: слот
  должен помещаться в собственный рабочий день, независимо от `include_self`
  (см. следующий пункт). Отказ по своему ящику (`_get_free_busy_view`)
  остаётся фатальным (`translate_ews_error`) всегда — без него нет ни
  рабочего окна, ни смысла продолжать. Недоступные участники попадают в поле
  `unavailable` (`{"email": ..., "reason": ...}`), которое присутствует в
  ответе всегда (пустой список в одиночном режиме), а не только когда
  что-то пошло не так — стабильная форма ответа проще для LLM-клиента, чем
  условный ключ.
- **find_free_slots и `include_self`**: изначально свой ящик был
  безусловно частью пересечения занятости. Добавлен флаг
  `include_self: bool = True` — при `False` собственные busy-интервалы
  исключаются из подсчёта, чтобы можно было спросить «когда свободен
  коллега, независимо от моего календаря». **Рабочие часы при этом всё
  равно берутся из своего ящика** — явное решение пользователя, а не
  недосмотр; поэтому `own_view` (через `_get_free_busy_view`) запрашивается
  и остаётся фатальным при ошибке **независимо от `include_self`** — он
  определяет рабочее окно дня, даже если сам не входит в занятость.
  `include_self=False` вместе с пустым/отсутствующим `emails` — вырожденный
  запрос (проверять ничей календарь) и намеренно поднимает
  `InvalidArgumentError` **до** любых обращений к EWS: вернуть в этом
  случае «весь день свободен» было бы неотличимо от настоящего результата
  для LLM-клиента.
- **find_free_slots: наивное время от `GetUserAvailability` — не UTC.**
  Найден на живом Exchange баг: `_busy_intervals_within` помечал naive
  datetime как `UTC` перед конвертацией в целевую таймзону — потенциальный
  сдвиг на величину offset (для `Europe/Moscow` — 3 часа). Причина: fake
  account в `GetUserAvailability._elem_to_obj`
  (`exchangelib/services/get_user_availability.py`) —
  `namedtuple("Account", ["default_timezone"])(default_timezone=self.tzinfo)`,
  где `self.tzinfo` — таймзона **запроса** (`day_start.tzinfo`, т.е.
  `EWSTimeZone.from_zoneinfo(config.timezone)`). `DateTimeField.from_xml`
  (`exchangelib/fields.py`) для naive-значений делает
  `local_dt.replace(tzinfo=account.default_timezone)` — то есть naive время
  от EWS уже wall-clock **в запрошенной таймзоне**, не в UTC. Исправлено:
  `start.replace(tzinfo=tz)` вместо `ZoneInfo("UTC")` (то же для `end`).
  На практике ветка обычно не срабатывает (exchangelib чаще всего сам
  проставляет offset), поэтому баг был незаметен на своём календаре — но
  мог тихо сработать для чужого free/busy.
- **find_free_slots и `reason`**: поле присутствует в ответе **всегда**
  (не только при `debug`) и объясняет пустой `slots`, вместо того чтобы
  заставлять читателя (LLM или человека) гадать «выходной это или всё
  занято». Значения и порядок проверки в `_compute_reason`:
  `all_participants_unavailable` (проверяется **первым**, до `slots` —
  см. следующий пункт про связанный latent-баг) → `ok` (`slots` непуст) →
  `only_tentative` → `no_window_fits_duration` (свободные окна есть, но
  короче `duration_min`) → `fully_busy`. Порядок важен: `all_participants_
  unavailable` обязан идти раньше `ok`, иначе он никогда не сработает
  (объясняется ниже).
- **find_free_slots: latent-баг с `include_self=False` + все участники
  недоступны.** Если при `include_self=False` free/busy не удалось прочитать
  ни у одного email из `unavailable`, множество `busy_views` пусто —
  `hard_busy`/`tentative_busy` пусты, и весь рабочий день считается
  свободным. Это ложноположительный результат: календарь фактически не
  проверялся ни у кого. Раньше это было неотличимо от настоящего «весь
  день свободен». Теперь `_compute_reason` ловит этот случай **до**
  проверки `slots` и возвращает `"all_participants_unavailable"` — сами
  `slots` при этом всё ещё содержат (некорректный) полный рабочий день,
  но `reason` предупреждает вызывающего не доверять им. Тест:
  `test_reason_all_participants_unavailable`.
- **find_free_slots и `debug=True` (диагностика).** Добавлен по итогам
  расследования: `find_free_slots(emails=[...], include_self=False)` на
  живом Exchange отдавал `reason: "fully_busy"` для коллеги, чей Outlook
  явно показывал промежутки между встречами — а `unavailable` был пуст
  (то есть `FreeBusyView` пришёл, но был неверно интерпретирован).
  Диагностировать вслепую невозможно: код читает **только**
  `view.calendar_events` и полностью игнорирует `view.view_type` и
  `view.merged` — строку цифр из `FREE_BUSY_CHOICES`
  (`exchangelib/fields.py`: `0=Free 1=Tentative 2=Busy 3=OOF 4=NoData
  5=WorkingElsewhere`), которую EWS может отдать вместо детальных событий
  (`FreeBusyView.merged`, `properties.py`) — обычно из-за пониженного
  уровня прав на free/busy чужого ящика (не ошибка, поэтому и не попадает
  в `unavailable`). При `debug=True` `_build_diagnostics`/`_view_diagnostics`
  собирают на **каждого** участника (включая `"self (<email>)"`, даже
  когда `counted_in_busy=False`): `view_type`, наличие и длину `merged`,
  количество и содержимое `raw_events` в локальной таймзоне с их
  `busy_type`, гистограмму `busy_type`, и интервалы до/после отсечения по
  рабочему окну (`hard_busy_after_filter`) — так по одной live-проверке
  видно, пришли ли детальные события вообще, и не разошлись ли `raw_events`
  с посчитанными интервалами. `_collect_participant_views` для этого
  возвращает пары `(email, view)`, а не голые views — иначе диагностику
  нельзя было бы привязать к конкретному участнику.
  `_view_diagnostics` **никогда не поднимает исключение** — деградирует в
  `{"label": ..., "diagnostics_error": ...}`, потому что диагностика
  существует для отладки сломанного пути и не должна стать вторым
  источником отказа. `raw_events` обрезаются на 100 записей
  (`raw_events_truncated`). Флаг `debug` — не под тестами production-трафика:
  диагностика добавляет заметный объём в ответ (десятки событий × несколько
  участников), поэтому по умолчанию `False`. Следующий шаг после
  добавления диагностики — по её данным (в частности, `merged_present` при
  `calendar_events_count: 0` для чужого ящика) написать декодер
  `merged`-строки как fallback, когда детальные события Exchange не отдаёт.
- **Зависимость `mcp` закреплена как `mcp>=1.2.0,<2.0.0`** (`pyproject.toml`).
  Причина: на PyPI под именем `mcp` сейчас существует посторонний пакет
  `mcp==2.0.0` (не Anthropic MCP SDK — другие зависимости, `httpx2`/
  `mcp-types`), который не содержит `mcp.server.fastmcp`. Без верхней
  границы `uv lock` резолвил именно его, и `server.py` падал на импорте.
  **Важно:** эта граница не даёт узнать, когда выйдет настоящий Anthropic
  SDK `2.0.0+` — она молча блокирует апгрейд. Если/когда официальный `mcp`
  выпустит мажор 2.x, нужно вручную: (1) проверить, что пакет с этим именем
  на PyPI — снова официальный Anthropic SDK, а не сторонний тёзка; (2)
  поднять границу (`<2.0.0` → `<3.0.0` и т.д.); (3) прогнать полный набор
  тестов и ручную проверку — `fastmcp`-API мог измениться в мажорном
  релизе. Не снимать границу совсем и не поднимать её не глядя.
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
- **`WorkingPeriod.weekdays` — это индексы (int), а не строки.** Тот же класс ошибки, что и с `sender` выше: предположение о типе поля exchangelib без проверки. `_working_period_for_weekday` сравнивал `weekday_name in period.weekdays`, то есть строку `"Thursday"` со списком элементов `weekdays`. Но `WorkingPeriod.weekdays` объявлен как `EnumListField(field_uri="DayOfWeek", enum=WEEKDAY_NAMES)` (`exchangelib/properties.py`), а `EnumField.from_xml` (`exchangelib/fields.py`) возвращает `[self.enum.index(v) + 1 for v in val.split(" ")]` — **1-based индексы** в `WEEKDAY_NAMES = ("Monday", ..., "Sunday")` (`exchangelib/fields.py`). Четверг приходит как `4`, совпадения со строкой нет **никогда, ни для одного дня недели**. Последствие было максимальным: `_working_period_for_weekday` всегда возвращал `None`, `find_free_slots` всегда отдавал пустой результат с `reason: "no_working_hours_for_weekday"` — и для чужого ящика, и для своего, с самой первой реализации. Исправлено через `_weekday_names()`, которая нормализует обе формы (int-индексы и строки) к именам дней; строки принимаются, чтобы не сломаться, если другая версия/путь парсинга отдаст их.
- **Фейки в тестах обязаны повторять реальный контракт exchangelib.** Прямое следствие предыдущего пункта и главный урок: баг с `weekdays` не был пойман **ни одним** из ~60 тестов, потому что `FakeWorkingPeriod` в `tests/test_calendar_service.py` хранил ровно то, чем его инициализировали — строки (`FakeWorkingPeriod(["Wednesday"], ...)`). Весь набор тестов был зелёным при полностью нерабочем коде. Теперь `FakeWorkingPeriod` конвертирует имена дней в 1-based индексы в конструкторе — тесты по-прежнему читаются как `["Wednesday"]`, но исполняются против того представления, которое реально приходит от EWS. Проверено, что это работает: до фикса `_working_period_for_weekday` с новым фейком падало 30 тестов. При добавлении фейка для любого нового объекта exchangelib — сверять типы полей с исходниками библиотеки, а не с тем, как «логично» выглядит API.
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

## Тесты, Docker, ручная проверка EWS, ограничения проекта

См. [README.md](README.md) — команды тестов, curl-проверка EWS endpoint,
Docker-фрагмент конфига и раздел «Не делать» не дублируются здесь.
