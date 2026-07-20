#!/usr/bin/env bash
#
# Установочный скрипт gitlab-mcp (GitLab через сторонний сервер
# zereight/gitlab-mcp, Node/npm) для macOS (arm64) и Linux.
# Запуск:  bash setup.sh
# Прав администратора / sudo не требует.
#
set -euo pipefail

# --- Константы --------------------------------------------------------------
SERVER_KEY="gitlab-mcp"
VERSIONS_RAW_URL="https://raw.githubusercontent.com/ShDA009/mcp/master/mcp-versions.txt"
# Fallback-версия, если mcp-versions.txt никогда не удастся скачать (первый запуск
# без сети). Лаунчер подтягивает актуальную версию из репо при каждом старте.
FALLBACK_GITLAB_SPEC="@zereight/mcp-gitlab@^2.1"

# --- Цвета (без внешних зависимостей) ---------------------------------------
if [ -t 1 ]; then
  BOLD=$(printf '\033[1m'); RED=$(printf '\033[31m'); GRN=$(printf '\033[32m')
  YEL=$(printf '\033[33m'); RST=$(printf '\033[0m')
else
  BOLD=""; RED=""; GRN=""; YEL=""; RST=""
fi
info()  { printf '%s\n' "${BOLD}$*${RST}"; }
ok()    { printf '%s\n' "${GRN}$*${RST}"; }
warn()  { printf '%s\n' "${YEL}$*${RST}"; }
err()   { printf '%s\n' "${RED}$*${RST}" >&2; }
die()   { err "$*"; exit 1; }

OS="$(uname -s)"
case "$OS" in
  Darwin)
    ARCH="$(uname -m)"
    if [ "$ARCH" != "arm64" ]; then
      die "Поддерживается только macOS на Apple Silicon (arm64). Обнаружено: $ARCH.
Intel/Rosetta не поддерживается — обратитесь к администратору."
    fi
    PLATFORM="macOS"
    ;;
  Linux)  PLATFORM="Linux" ;;
  *)      die "Неизвестная ОС: $OS. Скрипт рассчитан на macOS (arm64) и Linux." ;;
esac

info "== Установка gitlab-mcp для $PLATFORM =="

# --- Проверка базовых утилит: curl, node, npx --------------------------------
command -v curl >/dev/null 2>&1 || die "Не найден 'curl' — установите его и повторите."

if ! command -v node >/dev/null 2>&1 || ! command -v npx >/dev/null 2>&1; then
  die "Не найден Node.js/npx. Этот сервер (в отличие от остальных в репозитории)
написан на Node и требует установленного Node.js >= 18 — 'uv' его не ставит.
Установите Node.js (https://nodejs.org, или через nvm/brew) и запустите скрипт снова."
fi
NODE_VERSION="$(node --version)"
ok "node: $NODE_VERSION, npx: $(npx --version)"

# --- 1. Пути конфигов --------------------------------------------------------
if [ "$PLATFORM" = "macOS" ]; then
  CLINE_DIR="$HOME/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings"
else
  CLINE_DIR="$HOME/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings"
fi
CLINE_CFG="$CLINE_DIR/cline_mcp_settings.json"

# --- 2. Каталог, .env и лаунчер сервера --------------------------------------
CONF_DIR="$HOME/.config/gitlab-mcp"
ENV_FILE="$CONF_DIR/.env"
LAUNCH_FILE="$CONF_DIR/launch.sh"
mkdir -p "$CONF_DIR"
chmod 700 "$CONF_DIR" 2>/dev/null || true

env_get() {
  local key="$1"
  [ -f "$ENV_FILE" ] || return 1
  local line
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n1 || true)"
  [ -n "$line" ] || return 1
  printf '%s' "${line#*=}"
}

CUR_API_URL="$(env_get GITLAB_API_URL || true)"
HAVE_TOKEN="no"; env_get GITLAB_PERSONAL_ACCESS_TOKEN >/dev/null 2>&1 && HAVE_TOKEN="yes"

CHANGE="yes"
if [ -f "$ENV_FILE" ] && [ -n "$CUR_API_URL" ]; then
  info "Найден существующий конфиг: $ENV_FILE"
  printf '  GITLAB_API_URL                = %s\n' "${CUR_API_URL:-<не задан>}"
  printf '  GITLAB_PERSONAL_ACCESS_TOKEN  = %s\n' "$([ "$HAVE_TOKEN" = yes ] && echo '******** (сохранён)' || echo '<не задан>')"
  printf '%s' "Изменить креды? (y/n, по умолчанию n) "
  read -r ans
  case "$ans" in y|Y|yes|YES) CHANGE="yes";; *) CHANGE="no";; esac
fi

read_nonempty() {  # prompt default -> echoes value on stdout, prompt on stderr
  local prompt="$1" def="${2:-}" val
  while :; do
    if [ -n "$def" ]; then
      printf '%s [%s]: ' "$prompt" "$def" >&2
    else
      printf '%s: ' "$prompt" >&2
    fi
    if ! read -r val; then
      err "Ввод прерван (нет данных). Настройка не завершена." >&2
      exit 1
    fi
    [ -z "$val" ] && [ -n "$def" ] && val="$def"
    [ -n "$val" ] && { printf '%s' "$val"; return 0; }
    warn "Значение не может быть пустым." >&2
  done
}

if [ "$CHANGE" = "yes" ]; then
  info "Введите параметры подключения к GitLab:"
  GITLAB_API_URL="$(read_nonempty "  GitLab API URL (https://gitlab.example.com/api/v4)" "${CUR_API_URL:-}")"
  while :; do
    printf '  Personal Access Token (ввод скрыт): '
    if ! read -rs GITLAB_PERSONAL_ACCESS_TOKEN; then
      printf '\n'; die "Ввод прерван (нет данных). Настройка не завершена."
    fi
    printf '\n'
    [ -n "$GITLAB_PERSONAL_ACCESS_TOKEN" ] && break
    warn "Токен не может быть пустым."
  done
else
  GITLAB_API_URL="$CUR_API_URL"
  GITLAB_PERSONAL_ACCESS_TOKEN="$(env_get GITLAB_PERSONAL_ACCESS_TOKEN || true)"
  ok "Креды оставлены без изменений."
fi

# --- 3. Записать .env (атомарно, права 600) ---------------------------------
# GITLAB_PERMISSION_MODE=full — эквивалент прежнего GITLAB_READ_ONLY_MODE=false
# (upstream 2.1.x объявил READ_ONLY_MODE устаревшим в пользу PERMISSION_MODE).
umask 177
TMP_ENV="$(mktemp "${CONF_DIR}/.env.XXXXXX")"
{
  printf 'GITLAB_API_URL=%s\n'               "$GITLAB_API_URL"
  printf 'GITLAB_PERSONAL_ACCESS_TOKEN=%s\n' "$GITLAB_PERSONAL_ACCESS_TOKEN"
  printf 'GITLAB_PERMISSION_MODE=full\n'
  printf 'USE_GITLAB_WIKI=true\n'
  printf 'USE_MILESTONE=true\n'
  printf 'USE_PIPELINE=true\n'
  printf 'NODE_TLS_REJECT_UNAUTHORIZED=0\n'
} > "$TMP_ENV"
mv -f "$TMP_ENV" "$ENV_FILE"
chmod 600 "$ENV_FILE" 2>/dev/null || true
umask 022
ok "Креды сохранены в $ENV_FILE (chmod 600)."

# --- 4. Сгенерировать лаунчер -------------------------------------------------
# Лаунчер подтягивает версию из mcp-versions.txt в репо при каждом старте — так
# обновление версии доезжает до сотрудника без переустановки. Из сети берётся
# ТОЛЬКО строка со спецификатором пакета, она не исполняется как код: файл
# парсится через `.` (source), но перед этим проверяется регуляркой на
# допустимые символы — если что-то не так, используется fallback.
umask 077
TMP_LAUNCH="$(mktemp "${CONF_DIR}/launch.sh.XXXXXX")"
cat > "$TMP_LAUNCH" <<LAUNCHEOF
#!/usr/bin/env bash
set -euo pipefail
CONF_DIR="\$HOME/.config/gitlab-mcp"
CACHE="\$CONF_DIR/mcp-versions.txt"
RAW_URL="$VERSIONS_RAW_URL"
FALLBACK_SPEC="$FALLBACK_GITLAB_SPEC"

# 1) Попробовать обновить кеш версии из репо (короткий таймаут — не вешать старт).
TMP_CACHE="\$(mktemp "\${CONF_DIR}/mcp-versions.txt.XXXXXX" 2>/dev/null || true)"
if [ -n "\$TMP_CACHE" ] && curl -sf --max-time 3 -o "\$TMP_CACHE" "\$RAW_URL" 2>/dev/null; then
  mv -f "\$TMP_CACHE" "\$CACHE"
else
  [ -n "\$TMP_CACHE" ] && rm -f "\$TMP_CACHE" 2>/dev/null || true
fi

# 2) Взять GITLAB_SPEC из кеша, только если строка выглядит как безопасный
#    пакетный спецификатор (буквы/цифры/@/./_/-/,/=/</>/^/~ и слэш для npm-скоупов).
GITLAB_SPEC="\$FALLBACK_SPEC"
if [ -f "\$CACHE" ]; then
  line="\$(grep -E '^GITLAB_SPEC=' "\$CACHE" | tail -n1 || true)"
  value="\${line#GITLAB_SPEC=}"
  value="\${value%\\"}"; value="\${value#\\"}"
  if printf '%s' "\$value" | grep -qE '^[A-Za-z0-9@/._,=<>^~-]+\$'; then
    GITLAB_SPEC="\$value"
  fi
fi

set -a
. "\$CONF_DIR/.env"
set +a
exec npx -y "\$GITLAB_SPEC" "\$@"
LAUNCHEOF
mv -f "$TMP_LAUNCH" "$LAUNCH_FILE"
chmod 700 "$LAUNCH_FILE"
umask 022
ok "Лаунчер сгенерирован: $LAUNCH_FILE"

# --- 5. Обновить конфиг Cline идемпотентно ----------------------------------
mkdir -p "$CLINE_DIR"
[ -f "$CLINE_CFG" ] || printf '{\n  "mcpServers": {}\n}\n' > "$CLINE_CFG"

info "Обновляю конфиг Cline: $CLINE_CFG"
PY=""
for p in python3 python; do command -v "$p" >/dev/null 2>&1 && { PY="$p"; break; }; done
[ -n "$PY" ] || die "Не найден python3 — он нужен для безопасного обновления JSON-конфига Cline."

CLINE_CFG="$CLINE_CFG" SERVER_KEY="$SERVER_KEY" LAUNCH_FILE="$LAUNCH_FILE" \
"$PY" - <<'PYEOF'
import json, os

path = os.environ["CLINE_CFG"]
key  = os.environ["SERVER_KEY"]

try:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    data = {}

if not isinstance(data, dict):
    data = {}
servers = data.get("mcpServers")
if not isinstance(servers, dict):
    servers = {}
    data["mcpServers"] = servers

# Обновляем секцию на месте — не дублируем. Ни версия, ни креды НЕ попадают в
# этот JSON: всё это внутри launch.sh и .env в ~/.config/gitlab-mcp/.
servers[key] = {
    "command": os.environ["LAUNCH_FILE"],
    "args": [],
    "disabled": False,
    "transportType": "stdio",
}

tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
os.replace(tmp, path)
try:
    os.chmod(path, 0o600)
except OSError:
    pass
print("  секция '%s' обновлена (лаунчер %s)" % (key, os.environ["LAUNCH_FILE"]))
PYEOF

# --- 6. Проверочный вызов ---------------------------------------------------
# gitlab-mcp не поддерживает --help и падает без валидных кредов — вместо
# --help запускаем сервер на несколько секунд и ищем в его логе подтверждение
# успешного старта ("Configuration validation passed" / "stdio transport"),
# затем завершаем процесс. Это ожидаемое поведение именно для этого сервера.
info "Проверяю, что пакет ставится и запускается..."
selftest_ok="no"
SELFTEST_LOG="$(mktemp)"
"$LAUNCH_FILE" < /dev/null > "$SELFTEST_LOG" 2>&1 &
SELFTEST_PID=$!
# npx может ставить пакет с нуля при первом запуске (нет локального кеша) —
# ждём с запасом, повторные запуски будут почти мгновенными за счёт кеша npm.
sleep 20
kill "$SELFTEST_PID" 2>/dev/null || true
wait "$SELFTEST_PID" 2>/dev/null || true
if grep -qi "configuration validation passed\|stdio transport" "$SELFTEST_LOG"; then
  selftest_ok="yes"
fi
rm -f "$SELFTEST_LOG"

if [ "$selftest_ok" = "yes" ]; then
  ok "Проверочный запуск успешен."
else
  warn "Проверочный запуск не подтвердил успешный старт за 20 секунд."
  warn "Если Cline не подключится:"
  warn "  - проверьте доступ в интернет / к github.com и registry.npmjs.org (прокси);"
  warn "  - проверьте, что VPN подключён (для доступа к GitLab);"
  warn "  - проверьте правильность GITLAB_API_URL и токена."
fi

# --- 7. Итог ----------------------------------------------------------------
echo
ok "== Готово =="
printf '%s\n' "  node/npx:   $(command -v node)"
printf '%s\n' "  Конфиг:     $ENV_FILE"
printf '%s\n' "  Лаунчер:    $LAUNCH_FILE"
printf '%s\n' "  Cline:      $CLINE_CFG (сервер '$SERVER_KEY')"
echo
info "Дальше:"
printf '%s\n' "  1. Полностью перезапустите VS Code (и Cline)."
printf '%s\n' "  2. В Cline проверьте, что MCP-сервер '$SERVER_KEY' активен."
if [ "$PLATFORM" = "macOS" ]; then
  printf '%s\n' "  3. Если GitLab недоступен — убедитесь, что подключён корпоративный VPN."
else
  printf '%s\n' "  3. Если GitLab недоступен — убедитесь, что подключён корпоративный VPN / есть доступ в корп. сеть."
fi
echo
printf '%s\n' "При проблемах обращайтесь к администратору gitlab-mcp."
