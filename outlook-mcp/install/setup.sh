#!/usr/bin/env bash
#
# Установочный скрипт outlook-mcp (EWS MCP-сервер) для macOS (arm64) и Linux.
# Запуск:  bash setup.sh
# Прав администратора / sudo не требует.
#
set -euo pipefail

# --- Константы --------------------------------------------------------------
GIT_URL="git+https://github.com/ShDA009/mcp.git#subdirectory=outlook-mcp"
MCP_ENTRY="ews-mcp-server"
SERVER_KEY="outlook-mcp"
UV_INSTALLER="https://astral.sh/uv/install.sh"

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

info "== Установка outlook-mcp для $PLATFORM =="

# --- Проверка базовых утилит ------------------------------------------------
command -v curl >/dev/null 2>&1 || die "Не найден 'curl' — установите его и повторите."

# --- 1. Поиск uv ------------------------------------------------------------
find_uv() {
  # 1) явно в PATH
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return 0
  fi
  # 2) типичные места установки
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
      # инсталлятор кладёт в ~/.local/bin
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

# uvx лежит рядом с uv
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

# --- 3. Каталог и .env сервера ----------------------------------------------
CONF_DIR="$HOME/.config/outlook-mcp"
ENV_FILE="$CONF_DIR/.env"
mkdir -p "$CONF_DIR"
chmod 700 "$CONF_DIR" 2>/dev/null || true

# читаем текущее значение из .env, если есть
env_get() {
  local key="$1"
  [ -f "$ENV_FILE" ] || return 1
  # берём последнее присвоение key=...
  local line
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n1 || true)"
  [ -n "$line" ] || return 1
  printf '%s' "${line#*=}"
}

CUR_URL="$(env_get EWS_URL || true)"
CUR_USER="$(env_get EWS_USERNAME || true)"
CUR_EMAIL="$(env_get EWS_EMAIL || true)"
HAVE_PASS="no"; env_get EWS_PASSWORD >/dev/null 2>&1 && HAVE_PASS="yes"

CHANGE="yes"
if [ -f "$ENV_FILE" ] && [ -n "$CUR_USER" ]; then
  info "Найден существующий конфиг: $ENV_FILE"
  printf '  EWS_URL      = %s\n' "${CUR_URL:-<не задан>}"
  printf '  EWS_USERNAME = %s\n' "${CUR_USER:-<не задан>}"
  printf '  EWS_EMAIL    = %s\n' "${CUR_EMAIL:-<не задан>}"
  printf '  EWS_PASSWORD = %s\n' "$([ "$HAVE_PASS" = yes ] && echo '******** (сохранён)' || echo '<не задан>')"
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
      # stdin исчерпан (EOF) — не зацикливаемся
      err "Ввод прерван (нет данных). Настройка не завершена." >&2
      exit 1
    fi
    [ -z "$val" ] && [ -n "$def" ] && val="$def"
    [ -n "$val" ] && { printf '%s' "$val"; return 0; }
    warn "Значение не может быть пустым." >&2
  done
}

if [ "$CHANGE" = "yes" ]; then
  info "Введите параметры подключения к Exchange (EWS):"
  EWS_URL="$(read_nonempty "  EWS URL (https://.../EWS/Exchange.asmx)" "${CUR_URL:-}")"
  EWS_USERNAME="$(read_nonempty "  Логин (EWS_USERNAME, без домена)" "${CUR_USER:-}")"
  EWS_EMAIL="$(read_nonempty "  Email ящика (EWS_EMAIL, полный SMTP)" "${CUR_EMAIL:-}")"
  # пароль — скрытый ввод
  while :; do
    printf '  Пароль (ввод скрыт): '
    if ! read -rs EWS_PASSWORD; then
      printf '\n'; die "Ввод прерван (нет данных). Настройка не завершена."
    fi
    printf '\n'
    [ -n "$EWS_PASSWORD" ] && break
    warn "Пароль не может быть пустым."
  done
else
  # оставляем как есть — перечитаем всё из .env
  EWS_URL="$CUR_URL"; EWS_USERNAME="$CUR_USER"; EWS_EMAIL="$CUR_EMAIL"
  EWS_PASSWORD="$(env_get EWS_PASSWORD || true)"
  ok "Креды оставлены без изменений."
fi

# --- 4. Записать .env (атомарно, права 600) ---------------------------------
umask 177
TMP_ENV="$(mktemp "${CONF_DIR}/.env.XXXXXX")"
{
  printf 'EWS_URL=%s\n'      "$EWS_URL"
  printf 'EWS_USERNAME=%s\n' "$EWS_USERNAME"
  printf 'EWS_EMAIL=%s\n'    "$EWS_EMAIL"
  printf 'EWS_PASSWORD=%s\n' "$EWS_PASSWORD"
} > "$TMP_ENV"
mv -f "$TMP_ENV" "$ENV_FILE"
chmod 600 "$ENV_FILE" 2>/dev/null || true
umask 022
ok "Креды сохранены в $ENV_FILE (chmod 600)."

# --- 5. Обновить конфиг Cline идемпотентно ----------------------------------
mkdir -p "$CLINE_DIR"
[ -f "$CLINE_CFG" ] || printf '{\n  "mcpServers": {}\n}\n' > "$CLINE_CFG"

info "Обновляю конфиг Cline: $CLINE_CFG"
# Правку JSON делаем через Python (есть на macOS и почти любом Linux).
PY=""
for p in python3 python; do command -v "$p" >/dev/null 2>&1 && { PY="$p"; break; }; done
[ -n "$PY" ] || die "Не найден python3 — он нужен для безопасного обновления JSON-конфига Cline."

CLINE_CFG="$CLINE_CFG" SERVER_KEY="$SERVER_KEY" UVX_BIN="$UVX_BIN" \
GIT_URL="$GIT_URL" MCP_ENTRY="$MCP_ENTRY" \
"$PY" - <<'PYEOF'
import json, os, sys

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

# Обновляем секцию на месте — не дублируем. Креды НЕ попадают в этот JSON:
# outlook_mcp/config.py сам читает их из ~/.config/outlook-mcp/.env при старте.
servers[key] = {
    "command": os.environ["UVX_BIN"],
    "args": ["--from", os.environ["GIT_URL"], os.environ["MCP_ENTRY"]],
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
print("  секция '%s' обновлена (командой %s)" % (key, os.environ["UVX_BIN"]))
PYEOF

# --- 6. Проверочный вызов ---------------------------------------------------
info "Проверяю, что пакет ставится и запускается (uvx ... --help)..."
if "$UVX_BIN" --from "$GIT_URL" "$MCP_ENTRY" --help >/dev/null 2>&1; then
  ok "Проверочный запуск успешен."
else
  # --help может не поддерживаться сервером; это не критично для stdio MCP.
  warn "Проверочный запуск завершился с ненулевым кодом."
  warn "Это не всегда ошибка (сервер может не поддерживать --help). Если Cline не подключится:"
  warn "  - проверьте доступ в интернет / к github.com (прокси);"
  warn "  - проверьте, что VPN подключён (для доступа к EWS)."
fi

# --- 7. Итог ----------------------------------------------------------------
echo
ok "== Готово =="
printf '%s\n' "  uv/uvx:     $UVX_BIN"
printf '%s\n' "  Конфиг:     $ENV_FILE"
printf '%s\n' "  Cline:      $CLINE_CFG (сервер '$SERVER_KEY')"
echo
info "Дальше:"
printf '%s\n' "  1. Полностью перезапустите VS Code (и Cline)."
printf '%s\n' "  2. В Cline проверьте, что MCP-сервер '$SERVER_KEY' активен."
if [ "$PLATFORM" = "macOS" ]; then
  printf '%s\n' "  3. Если EWS недоступен — убедитесь, что подключён корпоративный VPN."
else
  printf '%s\n' "  3. Если EWS недоступен — убедитесь, что подключён корпоративный VPN / есть доступ в корп. сеть."
fi
echo
printf '%s\n' "При проблемах обращайтесь к администратору outlook-mcp."
