# MCP Servers

Коллекция MCP-серверов для подключения AI-ассистентов (Cline, OpenCode) к корпоративным сервисам.

## Серверы

| Сервер | Описание | Протокол |
|---|---|---|
| [mcp-atlassian](mcp-atlassian/) | Jira + Confluence | REST API + PAT |
| [zephyr-mcp](zephyr-mcp/) | Zephyr Scale (ATM) | REST API + Bearer PAT |
| [outlook-mcp](outlook-mcp/) | Exchange (календарь + почта) | EWS (SOAP/NTLM) |
| [gitlab-mcp](gitlab-mcp/) | GitLab | REST API + PAT |

## Быстрый старт

### 1. Скопировать `.env.example` и заполнить

```bash
cp mcp-atlassian/.env.example mcp-atlassian/.env
cp zephyr-mcp/.env.example zephyr-mcp/.env
cp outlook-mcp/.env.example outlook-mcp/.env
cp gitlab-mcp/.env.example gitlab-mcp/.env
```

**Важно:** `.env` файлы не должны попадать в git — они заигнорированы в `.gitignore`.

### 2. Собрать Docker-образы

```bash
cd outlook-mcp && docker build -t outlook-mcp:latest .
cd ../zephyr-mcp && docker build -t zephyr-mcp:latest .
```

`mcp-atlassian` и `gitlab-mcp` используют готовые образы из Docker Hub / GHCR.

**Без Docker (uvx):** `outlook-mcp` и `zephyr-mcp` — собственные Python-серверы,
их можно поставить установочным скриптом из `install/` в каждой папке (Windows/
macOS/Linux) — они запускают сервер через `uvx` и сами прописывают его в Cline.
См. [outlook-mcp/install/README.md](outlook-mcp/install/README.md) и
[zephyr-mcp/install/README.md](zephyr-mcp/install/README.md). `mcp-atlassian` и
`gitlab-mcp` — сторонние (Python/Node), остаются на Docker.

### 3. Подключить в AI-ассистенте

Рабочие конфиги (с абсолютными путями):
- OpenCode: `~/.config/opencode/opencode.jsonc`
- Cline: `cline_mcp_settings.json` (в настройках VS Code)

## Структура

```
├── README.md              ← этот файл
├── .gitignore
├── mcp-atlassian/         ← Jira + Confluence
│   ├── .env.example
│   └── .gitignore
├── zephyr-mcp/            ← Zephyr Scale (тест-кейсы)
│   ├── README.md
│   ├── CLAUDE.md
│   ├── .env.example
│   └── .gitignore
├── outlook-mcp/           ← Exchange (календарь + почта)
│   ├── README.md
│   ├── CLAUDE.md
│   ├── .env.example
│   └── .gitignore
└── gitlab-mcp/            ← GitLab
    ├── .env.example
    └── .gitignore
```

## Безопасность

- Секреты хранятся **только** в `.env` файлах (локально, не в git)
- Конфиги используют `--env-file`, а не `-e` (токены не попадают в промпт AI-модели)
- В репозитории — только `.env.example` с плейсхолдерами
- Корпоративные URL и токены не зашиты в код

## Требования

- **Docker** — для запуска серверов в контейнерах
- **VPN / корпоративная сеть** — для доступа к внутренним сервисам
- **Cline** или **OpenCode** — AI-ассистент с поддержкой MCP

## Лицензия

[MIT](LICENSE)
