#!/usr/bin/env bash
#
# Установочный скрипт grafana-mcp (Grafana: дашборды, датасорсы, алерты,
# через сторонний сервер grafana/mcp-grafana) для macOS (arm64) и Linux.
# Запуск:  bash setup.sh
# Прав администратора / sudo не требует.
#
set -euo pipefail

# --- Константы --------------------------------------------------------------
SERVER_KEY="grafana"
UV_INSTALLER="https://astral.sh/uv/install.sh"
VERSIONS_RAW_URL="https://raw.githubusercontent.com/ShDA009/mcp/master/mcp-versions.txt"
# Fallback-версия, если mcp-versions.txt никогда не удастся скачать (первый запуск
# без сети). Подтягивается лаунчером из репо при каждом старте — актуальную
# версию сотрудники получают без переустановки, см. install/README.md.
FALLBACK_GRAFANA_SPEC="mcp-grafana>=2.0,<2.1"

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

info "== Установка grafana-mcp для $PLATFORM =="

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
# Cline хранит конфиг в двух разных местах в зависимости от версии/сборки:
#   1) ~/.cline/data/settings/            — свежие версии (standalone-каталог);
#   2) <VS Code globalStorage>/...        — прежний путь внутри расширения.
# Пишем в тот, который реально существует, иначе сервер не появится в списке
# MCP. Если есть оба — берём более свежий по времени изменения: именно его
# Cline и перезаписывает при работе.
CLINE_NEW_DIR="$HOME/.cline/data/settings"
if [ "$PLATFORM" = "macOS" ]; then
  CLINE_VSCODE_DIR="$HOME/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings"
else
  CLINE_VSCODE_DIR="$HOME/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings"
fi

NEW_CFG="$CLINE_NEW_DIR/cline_mcp_settings.json"
VSCODE_CFG="$CLINE_VSCODE_DIR/cline_mcp_settings.json"

if [ -f "$NEW_CFG" ] && [ -f "$VSCODE_CFG" ]; then
  # Оба есть — выбираем тот, что менялся позже.
  if [ "$NEW_CFG" -nt "$VSCODE_CFG" ]; then
    CLINE_DIR="$CLINE_NEW_DIR"
  else
    CLINE_DIR="$CLINE_VSCODE_DIR"
  fi
elif [ -f "$NEW_CFG" ]; then
  CLINE_DIR="$CLINE_NEW_DIR"
elif [ -f "$VSCODE_CFG" ]; then
  CLINE_DIR="$CLINE_VSCODE_DIR"
elif [ -d "$CLINE_NEW_DIR" ]; then
  CLINE_DIR="$CLINE_NEW_DIR"
else
  # Ничего нет — Cline, вероятно, ещё не запускался. Создаём новый путь.
  CLINE_DIR="$CLINE_NEW_DIR"
fi
CLINE_CFG="$CLINE_DIR/cline_mcp_settings.json"
ok "Конфиг Cline: $CLINE_CFG"

# --- 3. Каталог, .env и лаунчер сервера --------------------------------------
CONF_DIR="$HOME/.config/grafana-mcp"
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

HAVE_CONFIG="no"
env_get GRAFANA_URL >/dev/null 2>&1 && env_get GRAFANA_SERVICE_ACCOUNT_TOKEN >/dev/null 2>&1 && HAVE_CONFIG="yes"

CHANGE="yes"
if [ -f "$ENV_FILE" ] && [ "$HAVE_CONFIG" = "yes" ]; then
  info "Найден существующий конфиг: $ENV_FILE"
  printf '  GRAFANA_URL                    = %s\n' "$(env_get GRAFANA_URL || true)"
  printf '  GRAFANA_SERVICE_ACCOUNT_TOKEN  = %s\n' '******** (сохранён)'
  printf '%s' "Изменить настройки? (y/n, по умолчанию n) "
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
  info "Введите параметры подключения к Grafana:"
  while :; do
    GRAFANA_URL="$(read_nonempty '  GRAFANA_URL (например https://grafana.example.com)')"
    case "$GRAFANA_URL" in
      http://*|https://*) break ;;
      *) warn "Адрес должен начинаться с https:// или http://." ;;
    esac
  done
  GRAFANA_URL="${GRAFANA_URL%/}"

  # Токен service account — секрет, читаем скрытым вводом.
  while :; do
    printf '  GRAFANA_SERVICE_ACCOUNT_TOKEN (ввод скрыт): '
    if ! read -rs GRAFANA_SERVICE_ACCOUNT_TOKEN; then
      printf '\n'; die "Ввод прерван (нет данных). Настройка не завершена."
    fi
    printf '\n'
    [ -n "$GRAFANA_SERVICE_ACCOUNT_TOKEN" ] && break
    warn "Значение не может быть пустым."
  done
else
  GRAFANA_URL="$(env_get GRAFANA_URL || true)"
  GRAFANA_SERVICE_ACCOUNT_TOKEN="$(env_get GRAFANA_SERVICE_ACCOUNT_TOKEN || true)"
  ok "Настройки оставлены без изменений."
fi

# --- 4. Записать .env (атомарно, права 600) ---------------------------------
umask 177
TMP_ENV="$(mktemp "${CONF_DIR}/.env.XXXXXX")"
{
  printf 'GRAFANA_URL=%s\n'                   "$GRAFANA_URL"
  printf 'GRAFANA_SERVICE_ACCOUNT_TOKEN=%s\n' "$GRAFANA_SERVICE_ACCOUNT_TOKEN"
  printf 'PYTHONUNBUFFERED=1\n'
} > "$TMP_ENV"
mv -f "$TMP_ENV" "$ENV_FILE"
chmod 600 "$ENV_FILE" 2>/dev/null || true
umask 022
ok "Настройки сохранены в $ENV_FILE (chmod 600)."

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
CONF_DIR="$CONF_DIR"
CACHE="\$CONF_DIR/mcp-versions.txt"
RAW_URL="$VERSIONS_RAW_URL"
FALLBACK_SPEC="$FALLBACK_GRAFANA_SPEC"

# 1) Попробовать обновить кеш версии из репо (короткий таймаут — не вешать старт).
TMP_CACHE="\$(mktemp "\${CONF_DIR}/mcp-versions.txt.XXXXXX" 2>/dev/null || true)"
if [ -n "\$TMP_CACHE" ] && curl -sf --max-time 3 -o "\$TMP_CACHE" "\$RAW_URL" 2>/dev/null; then
  mv -f "\$TMP_CACHE" "\$CACHE"
else
  [ -n "\$TMP_CACHE" ] && rm -f "\$TMP_CACHE" 2>/dev/null || true
fi

# 2) Взять GRAFANA_SPEC из кеша, только если строка выглядит как безопасный
#    пакетный спецификатор (буквы/цифры/@/./_/-/,/=/</>/^/~ и слэш для npm-скоупов).
GRAFANA_SPEC="\$FALLBACK_SPEC"
if [ -f "\$CACHE" ]; then
  line="\$(grep -E '^GRAFANA_SPEC=' "\$CACHE" | tail -n1 || true)"
  value="\${line#GRAFANA_SPEC=}"
  value="\${value%\\"}"; value="\${value#\\"}"
  if printf '%s' "\$value" | grep -qE '^[A-Za-z0-9@/._,=<>^~-]+\$'; then
    GRAFANA_SPEC="\$value"
  fi
fi

# 3) Прогрузить переменные из .env в окружение процесса: GRAFANA_URL и
#    GRAFANA_SERVICE_ACCOUNT_TOKEN сервер читает оттуда сам, поэтому в конфиге
#    Cline секретов нет.
set -a
. "\$CONF_DIR/.env"
set +a

# --tls-skip-verify: корпоративные самоподписанные сертификаты.
exec "$UVX_BIN" --from "\$GRAFANA_SPEC" mcp-grafana --tls-skip-verify "\$@"
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

# Обновляем секцию на месте — не дублируем. Ни версия, ни токен НЕ попадают в
# этот JSON: всё это внутри launch.sh и .env в ~/.config/grafana-mcp/.
#
# У Cline две схемы записи сервера, и версии их не понимают взаимно:
#   новая:  {"transport": {"type": "stdio", "command": ..., "args": []}, ...}
#   старая: {"command": ..., "args": [], "transportType": "stdio", ...}
# Подстраиваемся под то, что уже лежит в файле у соседних серверов, иначе Cline
# проигнорирует запись. Если соседей нет — пишем новую схему.
launch = os.environ["LAUNCH_FILE"]


def uses_new_schema(cfg):
    for name, srv in cfg.items():
        if name == key or not isinstance(srv, dict):
            continue
        if isinstance(srv.get("transport"), dict):
            return True
        if "transportType" in srv or "command" in srv:
            return False
    return True


if uses_new_schema(servers):
    entry = {
        "transport": {"type": "stdio", "command": launch, "args": []},
        "disabled": False,
        "timeout": 60,
    }
else:
    entry = {
        "command": launch,
        "args": [],
        "disabled": False,
        "transportType": "stdio",
    }

# Если сервер уже был в конфиге и его выключили вручную — не включаем обратно.
prev = servers.get(key)
if isinstance(prev, dict) and isinstance(prev.get("disabled"), bool):
    entry["disabled"] = prev["disabled"]

servers[key] = entry

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
  warn "  - проверьте, что VPN подключён (для доступа к Grafana)."
fi

# --- 8. Итог ----------------------------------------------------------------
echo
ok "== Готово =="
printf '%s\n' "  uv/uvx:       $UVX_BIN"
printf '%s\n' "  Конфиг:       $ENV_FILE"
printf '%s\n' "  Лаунчер:      $LAUNCH_FILE"
printf '%s\n' "  Grafana:      $GRAFANA_URL"
printf '%s\n' "  Cline:        $CLINE_CFG (сервер '$SERVER_KEY')"
echo
info "Дальше:"
printf '%s\n' "  1. Полностью перезапустите VS Code (и Cline)."
printf '%s\n' "  2. В Cline проверьте, что MCP-сервер '$SERVER_KEY' активен."
printf '%s\n' "  3. Если Grafana недоступна — убедитесь, что подключён корпоративный VPN."
echo
printf '%s\n' "При проблемах обращайтесь к администратору grafana-mcp."
