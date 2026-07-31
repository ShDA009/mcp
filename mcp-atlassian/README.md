# mcp-atlassian

MCP-сервер для Jira (Server/Data Center, PAT) и Confluence. **Сторонний код**, не наш — используем готовый пакет [sooperset/mcp-atlassian](https://github.com/sooperset/mcp-atlassian) (MIT, PyPI `mcp-atlassian`). Здесь — только конфигурация подключения и установочные скрипты, исходников сервера в этом репозитории нет.

Транспорт — stdio, потребитель — Cline. Запускается через `uvx` (рекомендуется) или в Docker (`ghcr.io/sooperset/mcp-atlassian`).

## Установка для сотрудников (uvx, без Docker)

Готовые установочные скрипты в [install/](install/) — под Windows, macOS (Apple Silicon) и Linux. Скрипт проверяет/ставит `uv`, спрашивает креды Jira и Confluence, сохраняет `.env` и генерирует **лаунчер** (`~/.config/mcp-atlassian/launch.sh` на macOS/Linux, `launch.ps1`/`launch.cmd` на Windows), который прописывается в Cline вместо прямого вызова `uvx`. Инструкция — [install/README.md](install/README.md).

### Почему лаунчер, а не `uvx` напрямую в конфиге Cline

Версия пакета (`ATLASSIAN_SPEC` — минорная ветка, см. ниже) не зашивается в `cline_mcp_settings.json`. Лаунчер при каждом старте сервера подтягивает файл [`mcp-versions.txt`](../mcp-versions.txt) из корня репозитория и берёт версию оттуда — если поднять диапазон там и запушить, обновление доезжает до всех сотрудников без переустановки, максимум за несколько минут (кеш GitHub CDN). Если сеть недоступна — используется локальный кеш последнего успешного запроса, а если кеша тоже нет (самый первый запуск без сети) — вшитый в лаунчер fallback.

Из сети лаунчер получает **только строку со спецификатором версии**; она не исполняется как код, а после проверки регуляркой на допустимые символы подставляется в команду запуска.

### Версии

Диапазон зафиксирован в [`../mcp-versions.txt`](../mcp-versions.txt) (`ATLASSIAN_SPEC`) — минорная ветка (например `mcp-atlassian>=0.23,<0.24`): патчи и security-фиксы внутри неё подтягиваются автоматически, переход на следующую минорную/мажорную ветку требует правки этого файла и коммита. Еженедельный workflow [`.github/workflows/check-upstream.yml`](../.github/workflows/check-upstream.yml) заводит issue, если у upstream вышла версия за пределами текущего диапазона.

## Сборка и запуск (Docker, альтернатива)

```bash
docker run -i --rm --env-file .env ghcr.io/sooperset/mcp-atlassian:latest
```

## Конфигурация (`.env`)

Скопировать [.env.example](.env.example) и заполнить реальными значениями:

```bash
CONFLUENCE_URL=https://wiki.example.com
CONFLUENCE_PERSONAL_TOKEN=changeme
JIRA_URL=https://jira.example.com
JIRA_PERSONAL_TOKEN=changeme

# self-signed сертификаты корпоративного Jira/Confluence
VERIFY_SSL=false
CONFLUENCE_SSL_VERIFY=false
JIRA_SSL_VERIFY=false
PYTHONHTTPSVERIFY=0
PYTHONUNBUFFERED=1
```

`.env` не должен попадать в репозиторий (заигнорирован в `.gitignore`). Установочные скрипты пишут его за вас в `~/.config/mcp-atlassian/.env`.

## Не делать

- Не патчить и не форкать upstream без явной необходимости — пин минорной ветки в `mcp-versions.txt` уже даёт защиту от несовместимых изменений.
- Не передавать креды через `-e`/`env`-секцию в конфиге Cline — только через `.env`, который сервер (или лаунчер, при uvx-запуске) читает сам.
