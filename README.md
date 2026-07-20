# MCP Servers

Коллекция MCP-серверов для подключения AI-ассистентов (Cline, OpenCode) к корпоративным сервисам.

## Серверы

| Сервер | Описание | Свой код? | Запуск |
|---|---|---|---|
| [outlook-mcp](outlook-mcp/) | Exchange (календарь + почта), EWS/NTLM | да | `uvx` из git |
| [zephyr-mcp](zephyr-mcp/) | Zephyr Scale (ATM), REST + Bearer PAT | да | `uvx` из git |
| [mcp-atlassian](mcp-atlassian/) | Jira + Confluence, REST + PAT | нет (сторонний) | `uvx` из PyPI |
| [gitlab-mcp](gitlab-mcp/) | GitLab, REST + PAT | нет (сторонний, Node) | `npx` из npm |

## Установка (рекомендуется): установочный скрипт, без Docker

Каждый сервер ставится независимо своим скриптом из `<сервер>/install/`:

```bash
outlook-mcp/install/setup.sh    # или setup.ps1 на Windows
zephyr-mcp/install/setup.sh
mcp-atlassian/install/setup.sh
gitlab-mcp/install/setup.sh
```

Подробная инструкция — в `install/README.md` каждого сервера:
[outlook-mcp](outlook-mcp/install/README.md) ·
[zephyr-mcp](zephyr-mcp/install/README.md) ·
[mcp-atlassian](mcp-atlassian/install/README.md) ·
[gitlab-mcp](gitlab-mcp/install/README.md).

Скрипт сам:
1. находит/ставит `uv` (для `gitlab-mcp` дополнительно нужен предустановленный
   Node.js >= 18 — его скрипт не ставит);
2. спрашивает креды и сохраняет их в `~/.config/<сервер>/.env`;
3. прописывает сервер в `cline_mcp_settings.json` — **без Docker и без
   секретов в JSON**: команда указывает либо на `uvx` напрямую (свои серверы),
   либо на сгенерированный лаунчер (сторонние серверы, см. ниже).

Повторный запуск безопасен — секция в конфиге Cline обновляется на месте, не
дублируется.

### Почему у сторонних серверов (`mcp-atlassian`, `gitlab-mcp`) есть лаунчер

Для своих серверов (`outlook-mcp`, `zephyr-mcp`) конфиг Cline вызывает `uvx`
напрямую — версия кода не фиксируется, `uvx` всегда берёт актуальный `master`
из git.

Для сторонних серверов версия пакета зафиксирована как **минорная ветка** в
[`mcp-versions.txt`](mcp-versions.txt) в корне репозитория:

```bash
ATLASSIAN_SPEC="mcp-atlassian>=0.23,<0.24"
GITLAB_SPEC="@zereight/mcp-gitlab@^2.1"
```

Установщик генерирует **лаунчер** (`~/.config/<сервер>/launch.sh`, на Windows
`launch.ps1`+`launch.cmd`), который при каждом старте сервера подтягивает
`mcp-versions.txt` из репозитория и берёт версию оттуда — правка файла и
`git push` доезжают до всех сотрудников без переустановки (обычно за
несколько минут, за счёт кеша GitHub CDN). Патчи и security-фиксы внутри
диапазона upstream выпускает сам, ничего делать не нужно; подъём на следующую
минорную/мажорную ветку — правка одной строки в `mcp-versions.txt` и коммит.

Если сеть на момент старта недоступна — используется локальный кеш последнего
успешного запроса, а если кеша тоже нет (первый запуск без сети) — fallback,
вшитый в сам лаунчер. Из сети берётся **только строка со спецификатором
версии** — она не исполняется как код, а перед подстановкой в команду
проверяется regex'ом на допустимые символы.

Раз в неделю [`.github/workflows/check-upstream.yml`](.github/workflows/check-upstream.yml)
сравнивает `mcp-versions.txt` с реальными версиями на PyPI/npm и заводит issue,
если у upstream вышла минорная/мажорная ветка за пределами текущего диапазона.

## Установка (альтернатива): Docker

Для `outlook-mcp` и `zephyr-mcp` собрать образ самостоятельно:

```bash
cd outlook-mcp && docker build -t outlook-mcp:latest .
cd ../zephyr-mcp && docker build -t zephyr-mcp:latest .
```

`mcp-atlassian` и `gitlab-mcp` используют готовые образы:
`ghcr.io/sooperset/mcp-atlassian`, `zereight050/gitlab-mcp`.

Креды — вручную, скопировать `.env.example` в `.env` и заполнить:

```bash
cp mcp-atlassian/.env.example mcp-atlassian/.env
cp zephyr-mcp/.env.example zephyr-mcp/.env
cp outlook-mcp/.env.example outlook-mcp/.env
cp gitlab-mcp/.env.example gitlab-mcp/.env
```

`.env` не должен попадать в git (заигнорирован в `.gitignore`). В конфиге
Cline/OpenCode — `docker run ... --env-file /абсолютный/путь/.env`, не `-e`
(иначе секреты попадают в системный промпт AI-модели).

## Подключение в AI-ассистенте

Рабочие конфиги (с абсолютными путями):
- Cline: `cline_mcp_settings.json` (в настройках VS Code) — правит установщик
  автоматически.
- OpenCode: `~/.config/opencode/opencode.jsonc` — правится вручную, установщики
  его не трогают.

## Структура

```
├── README.md               ← этот файл
├── mcp-versions.txt        ← версии mcp-atlassian/gitlab-mcp (минорные ветки)
├── .gitignore
├── .github/workflows/
│   └── check-upstream.yml  ← еженедельная проверка версий upstream
├── mcp-atlassian/          ← Jira + Confluence (сторонний код)
│   ├── README.md
│   ├── install/            ← setup.sh / setup.ps1 (uvx + лаунчер)
│   ├── .env.example
│   └── .gitignore
├── zephyr-mcp/             ← Zephyr Scale (тест-кейсы, свой код)
│   ├── README.md
│   ├── CLAUDE.md
│   ├── install/            ← setup.sh / setup.ps1 (uvx из git)
│   ├── .env.example
│   └── .gitignore
├── outlook-mcp/            ← Exchange (календарь + почта, свой код)
│   ├── README.md
│   ├── CLAUDE.md
│   ├── install/            ← setup.sh / setup.ps1 (uvx из git)
│   ├── .env.example
│   └── .gitignore
└── gitlab-mcp/             ← GitLab (сторонний код, Node)
    ├── README.md
    ├── install/             ← setup.sh / setup.ps1 (npx + лаунчер)
    ├── .env.example
    └── .gitignore
```

## Безопасность

- Секреты — только в `.env`, локально, не в git; в репозитории — только
  `.env.example` с плейсхолдерами.
- Без Docker — сервер (или лаунчер) сам читает `.env` при старте, в
  `cline_mcp_settings.json` секретов нет вовсе.
- С Docker — `--env-file`, не `-e`.
- Корпоративные URL и токены не зашиты в код.

## Требования

- **`uv`** — для запуска без Docker; установочные скрипты ставят его сами.
- **Node.js >= 18** — дополнительно, только для `gitlab-mcp` (не ставится
  автоматически).
- **Docker** — опционально, как альтернатива.
- **VPN / корпоративная сеть** — для доступа к внутренним сервисам.
- **Cline** или **OpenCode** — AI-ассистент с поддержкой MCP.

## Лицензия

[MIT](LICENSE)
