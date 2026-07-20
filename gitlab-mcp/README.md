# gitlab-mcp

MCP-сервер для GitLab (issues, MR, wiki, pipelines и т.д.). **Сторонний код**,
не наш — используем готовый пакет [zereight/gitlab-mcp](https://github.com/zereight/gitlab-mcp)
(MIT, npm `@zereight/mcp-gitlab`). Здесь — только конфигурация подключения и
установочные скрипты, исходников сервера в этом репозитории нет.

Транспорт — stdio, потребитель — Cline. Запускается через `npx` (рекомендуется)
или в Docker (`zereight050/gitlab-mcp`).

**Единственный сервер в этом репозитории, написанный на Node** — требует
установленный Node.js >= 18 (`uv` его не ставит).

## Установка для сотрудников (npx, без Docker)

Готовые установочные скрипты в [install/](install/) — под Windows, macOS
(Apple Silicon) и Linux. Скрипт проверяет наличие Node.js/npx, спрашивает
креды GitLab, сохраняет `.env` и генерирует **лаунчер**
(`~/.config/gitlab-mcp/launch.sh` на macOS/Linux, `launch.ps1`/`launch.cmd` на
Windows), который прописывается в Cline. Инструкция — [install/README.md](install/README.md).

### Почему лаунчер обязателен (в отличие от других серверов)

`gitlab-mcp` не поддерживает флаг `--env-file` (в отличие от `mcp-atlassian`) —
креды можно передать только через переменные окружения процесса. Лаунчер
экспортирует их из `.env` перед запуском `npx`, поэтому в `cline_mcp_settings.json`
секретов нет вовсе.

Дополнительно лаунчер при каждом старте подтягивает файл
[`mcp-versions.txt`](../mcp-versions.txt) из корня репозитория и берёт версию пакета
оттуда — если поднять диапазон там и запушить, обновление доезжает до всех
сотрудников без переустановки. Если сеть недоступна — используется локальный
кеш последнего успешного запроса, а если кеша тоже нет (самый первый запуск
без сети) — вшитый в лаунчер fallback. Из сети берётся **только строка со
спецификатором версии**; она не исполняется как код, а после проверки
регуляркой на допустимые символы подставляется в команду запуска.

### Версии

Диапазон зафиксирован в [`../mcp-versions.txt`](../mcp-versions.txt) (`GITLAB_SPEC`) —
минорная ветка (например `@zereight/mcp-gitlab@^2.1`): патчи подтягиваются
автоматически, переход на следующую минорную/мажорную ветку требует правки
этого файла и коммита. Еженедельный workflow
[`.github/workflows/check-upstream.yml`](../.github/workflows/check-upstream.yml)
заводит issue, если у upstream вышла версия за пределами текущего диапазона.

## Сборка и запуск (Docker, альтернатива)

```bash
docker run -i --rm --env-file .env zereight050/gitlab-mcp
```

## Конфигурация (`.env`)

Скопировать [.env.example](.env.example) и заполнить реальными значениями:

```bash
GITLAB_PERSONAL_ACCESS_TOKEN=your_gitlab_token_here
GITLAB_API_URL=https://gitlab.example.com/api/v4
GITLAB_PERMISSION_MODE=full

# Feature flags
USE_GITLAB_WIKI=true
USE_MILESTONE=true
USE_PIPELINE=true

# SSL verification (self-signed корпоративные сертификаты)
NODE_TLS_REJECT_UNAUTHORIZED=0
```

> `GITLAB_PERMISSION_MODE` заменяет устаревший `GITLAB_READ_ONLY_MODE`
> (upstream объявил его deprecated в ветке 2.1.x). `full` = чтение и запись
> (эквивалент прежнего `GITLAB_READ_ONLY_MODE=false`), `readonly` = только чтение.

`.env` не должен попадать в репозиторий (заигнорирован в `.gitignore`).
Установочные скрипты пишут его за вас в `~/.config/gitlab-mcp/.env`.

## Не делать

- Не патчить и не форкать upstream без явной необходимости — пин минорной
  ветки в `mcp-versions.txt` уже даёт защиту от несовместимых изменений.
- Не передавать креды через `-e`/`env`-секцию в конфиге Cline — только через
  `.env`, который лаунчер читает сам.
