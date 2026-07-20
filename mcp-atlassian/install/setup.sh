#!/usr/bin/env bash
#
# Установочный скрипт mcp-atlassian (Jira + Confluence через сторонний
# сервер sooperset/mcp-atlassian) для macOS (arm64) и Linux.
# Запуск:  bash setup.sh
# Прав администратора / sudo не требует.
#
set -euo pipefail

# --- Константы --------------------------------------------------------------
SERVER_KEY="mcp-atlassian"
UV_INSTALLER="https://astral.sh/uv/install.sh"
VERSIONS_RAW_URL="https://raw.githubusercontent.com/ShDA009/mcp/master/mcp-versions.txt"
# Fallback-версия, если mcp-versions.txt никогда не удастся скачать (первый запуск
# без сети). Подтягивается лаунчером из репо при каждом старте — актуальную
# версию сотрудники получают без переустановки, см. install/README.md.
FALLBACK_ATLASSIAN_SPEC="mcp-atlassian>=0.23,<0.24"

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

info "== Установка mcp-atlassian для $PLATFORM =="

# --- Проверка базовых утилит ------------------------------------------------
command -v curl >/dev/null 2>&1 || die "Не найден 'curl' — установите его и повторите."

# --- 1. Поиск uv ------------------------------------------------------------
find_uv() {
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return 0
  fi
  local candidates=()
  if [ "$PLATFORM" = "macOS" ]; then
    candidates=("/opt/homebrew/bin/uv" "$HOME/.local/bin/uv")
  else
    candidates=("$HOME/.local/bin/uv" "/usr/local/bin/uv" "/usr/bin/uv")
  fi
  local c
  for c in "${candidates[@]}"; do
    if [ -x "$c" ]; then
      printf '%s\n' "$c"
      return 0
    fi
  done
  return 1
}

UV_BIN=""
if UV_BIN="$(find_uv)"; then
  ok "uv найден: $UV_BIN"
else
  warn "uv не найден ни в PATH, ни в типичных местах установки."
  printf '%s' "Установить uv в пользовательский профиль (sudo не требуется)? (y/n) "
  read -r ans
  case "$ans" in
    y|Y|yes|YES)
      info "Устанавливаю uv..."
      if ! curl -LsSf "$UV_INSTALLER" | sh; then
        die "Не удалось установить uv. Проверьте доступ в интернет / прокси и повторите."
      fi
      if UV_BIN="$(find_uv)"; then
        ok "uv установлен: $UV_BIN"
      else
        die "uv установлен, но не найден автоматически. Перезапустите терминал и запустите скрипт снова."
      fi
      ;;
    *)
      die "Без uv дальнейшая настройка невозможна. Ничего не изменено. Запустите скрипт снова, когда будете готовы установить uv."
      ;;
  esac
fi

UVX_BIN="$(dirname "$UV_BIN")/uvx"
[ -x "$UVX_BIN" ] || die "Найден uv ($UV_BIN), но рядом нет uvx. Переустановите uv."
ok "uvx: $UVX_BIN"

# --- 2. Путь к конфигу Cline ------------------------------------------------
if [ "$PLATFORM" = "macOS" ]; then
  CLINE_DIR="$HOME/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings"
else
  CLINE_DIR="$HOME/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings"
fi
CLINE_CFG="$CLINE_DIR/cline_mcp_settings.json"

# --- 3. Каталог, .env и лаунчер сервера --------------------------------------
CONF_DIR="$HOME/.config/mcp-atlassian"
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

CUR_CONFLUENCE_URL="$(env_get CONFLUENCE_URL || true)"
CUR_JIRA_URL="$(env_get JIRA_URL || true)"
HAVE_CONFLUENCE_TOKEN="no"; env_get CONFLUENCE_PERSONAL_TOKEN >/dev/null 2>&1 && HAVE_CONFLUENCE_TOKEN="yes"
HAVE_JIRA_TOKEN="no"; env_get JIRA_PERSONAL_TOKEN >/dev/null 2>&1 && HAVE_JIRA_TOKEN="yes"

CHANGE="yes"
if [ -f "$ENV_FILE" ] && [ -n "$CUR_JIRA_URL" ]; then
  info "Найден существующий конфиг: $ENV_FILE"
  printf '  CONFLUENCE_URL             = %s\n' "${CUR_CONFLUENCE_URL:-<не задан>}"
  printf '  CONFLUENCE_PERSONAL_TOKEN  = %s\n' "$([ "$HAVE_CONFLUENCE_TOKEN" = yes ] && echo '******** (сохранён)' || echo '<не задан>')"
  printf '  JIRA_URL                   = %s\n' "${CUR_JIRA_URL:-<не задан>}"
  printf '  JIRA_PERSONAL_TOKEN        = %s\n' "$([ "$HAVE_JIRA_TOKEN" = yes ] && echo '******** (сохранён)' || echo '<не задан>')"
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
  info "Введите параметры подключения к Atlassian (Jira Server/DC + Confluence):"
  CONFLUENCE_URL="$(read_nonempty "  Confluence URL (https://wiki.example.com)" "${CUR_CONFLUENCE_URL:-}")"
  while :; do
    printf '  Confluence Personal Access Token (ввод скрыт): '
    if ! read -rs CONFLUENCE_PERSONAL_TOKEN; then
      printf '\n'; die "Ввод прерван (нет данных). Настройка не завершена."
    fi
    printf '\n'
    [ -n "$CONFLUENCE_PERSONAL_TOKEN" ] && break
    warn "Токен не может быть пустым."
  done
  JIRA_URL="$(read_nonempty "  Jira URL (https://jira.example.com)" "${CUR_JIRA_URL:-}")"
  while :; do
    printf '  Jira Personal Access Token (ввод скрыт): '
    if ! read -rs JIRA_PERSONAL_TOKEN; then
      printf '\n'; die "Ввод прерван (нет данных). Настройка не завершена."
    fi
    printf '\n'
    [ -n "$JIRA_PERSONAL_TOKEN" ] && break
    warn "Токен не может быть пустым."
  done
else
  CONFLUENCE_URL="$CUR_CONFLUENCE_URL"
  CONFLUENCE_PERSONAL_TOKEN="$(env_get CONFLUENCE_PERSONAL_TOKEN || true)"
  JIRA_URL="$CUR_JIRA_URL"
  JIRA_PERSONAL_TOKEN="$(env_get JIRA_PERSONAL_TOKEN || true)"
  ok "Креды оставлены без изменений."
fi

# --- 4. Записать .env (атомарно, права 600) ---------------------------------
umask 177
TMP_ENV="$(mktemp "${CONF_DIR}/.env.XXXXXX")"
{
  printf 'CONFLUENCE_URL=%s\n'            "$CONFLUENCE_URL"
  printf 'CONFLUENCE_PERSONAL_TOKEN=%s\n' "$CONFLUENCE_PERSONAL_TOKEN"
  printf 'JIRA_URL=%s\n'                  "$JIRA_URL"
  printf 'JIRA_PERSONAL_TOKEN=%s\n'       "$JIRA_PERSONAL_TOKEN"
  printf 'VERIFY_SSL=false\n'
  printf 'CONFLUENCE_SSL_VERIFY=false\n'
  printf 'JIRA_SSL_VERIFY=false\n'
  printf 'PYTHONHTTPSVERIFY=0\n'
  printf 'PYTHONUNBUFFERED=1\n'
} > "$TMP_ENV"
mv -f "$TMP_ENV" "$ENV_FILE"
chmod 600 "$ENV_FILE" 2>/dev/null || true
umask 022
ok "Креды сохранены в $ENV_FILE (chmod 600)."

# --- 5. Сгенерировать лаунчер -------------------------------------------------
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
CONF_DIR="\$HOME/.config/mcp-atlassian"
CACHE="\$CONF_DIR/mcp-versions.txt"
RAW_URL="$VERSIONS_RAW_URL"
FALLBACK_SPEC="$FALLBACK_ATLASSIAN_SPEC"

# 1) Попробовать обновить кеш версии из репо (короткий таймаут — не вешать старт).
TMP_CACHE="\$(mktemp "\${CONF_DIR}/mcp-versions.txt.XXXXXX" 2>/dev/null || true)"
if [ -n "\$TMP_CACHE" ] && curl -sf --max-time 3 -o "\$TMP_CACHE" "\$RAW_URL" 2>/dev/null; then
  mv -f "\$TMP_CACHE" "\$CACHE"
else
  [ -n "\$TMP_CACHE" ] && rm -f "\$TMP_CACHE" 2>/dev/null || true
fi

# 2) Взять ATLASSIAN_SPEC из кеша, только если строка выглядит как безопасный
#    пакетный спецификатор (буквы/цифры/@/./_/-/,/=/</>/^/~ и слэш для npm-скоупов).
ATLASSIAN_SPEC="\$FALLBACK_SPEC"
if [ -f "\$CACHE" ]; then
  line="\$(grep -E '^ATLASSIAN_SPEC=' "\$CACHE" | tail -n1 || true)"
  value="\${line#ATLASSIAN_SPEC=}"
  value="\${value%\\"}"; value="\${value#\\"}"
  if printf '%s' "\$value" | grep -qE '^[A-Za-z0-9@/._,=<>^~-]+\$'; then
    ATLASSIAN_SPEC="\$value"
  fi
fi

set -a
. "\$CONF_DIR/.env"
set +a
exec "$UVX_BIN" --from "\$ATLASSIAN_SPEC" mcp-atlassian --env-file "\$CONF_DIR/.env" "\$@"
LAUNCHEOF
mv -f "$TMP_LAUNCH" "$LAUNCH_FILE"
chmod 700 "$LAUNCH_FILE"
umask 022
ok "Лаунчер сгенерирован: $LAUNCH_FILE"

# --- 6. Обновить конфиг Cline идемпотентно ----------------------------------
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
# этот JSON: всё это внутри launch.sh и .env в ~/.config/mcp-atlassian/.
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

# --- 7. Проверочный вызов ---------------------------------------------------
info "Проверяю, что пакет ставится и запускается (launch.sh --help)..."
if "$LAUNCH_FILE" --help >/dev/null 2>&1; then
  ok "Проверочный запуск успешен."
else
  warn "Проверочный запуск завершился с ненулевым кодом."
  warn "Если Cline не подключится:"
  warn "  - проверьте доступ в интернет / к github.com и pypi.org (прокси);"
  warn "  - проверьте, что VPN подключён (для доступа к Jira/Confluence)."
fi

# --- 8. Итог ----------------------------------------------------------------
echo
ok "== Готово =="
printf '%s\n' "  uv/uvx:     $UVX_BIN"
printf '%s\n' "  Конфиг:     $ENV_FILE"
printf '%s\n' "  Лаунчер:    $LAUNCH_FILE"
printf '%s\n' "  Cline:      $CLINE_CFG (сервер '$SERVER_KEY')"
echo
info "Дальше:"
printf '%s\n' "  1. Полностью перезапустите VS Code (и Cline)."
printf '%s\n' "  2. В Cline проверьте, что MCP-сервер '$SERVER_KEY' активен."
if [ "$PLATFORM" = "macOS" ]; then
  printf '%s\n' "  3. Если Jira/Confluence недоступны — убедитесь, что подключён корпоративный VPN."
else
  printf '%s\n' "  3. Если Jira/Confluence недоступны — убедитесь, что подключён корпоративный VPN / есть доступ в корп. сеть."
fi
echo
printf '%s\n' "При проблемах обращайтесь к администратору mcp-atlassian."
