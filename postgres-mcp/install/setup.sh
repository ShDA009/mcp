#!/usr/bin/env bash
#
# Установочный скрипт postgres-mcp (PostgreSQL: анализ, тюнинг, explain-планы,
# через сторонний сервер crystaldba/postgres-mcp) для macOS (arm64) и Linux.
# Запуск:  bash setup.sh
# Прав администратора / sudo не требует.
#
set -euo pipefail

# --- Константы --------------------------------------------------------------
# Имя инстанса. По умолчанию "postgres-mcp"; для второй и последующих БД
# передайте суффикс первым аргументом или через INSTANCE=<имя>:
#   bash setup.sh billing   -> сервер "postgres-mcp-billing",
#                              конфиг ~/.config/postgres-mcp-billing/
# Так одна БД = один инстанс, и они не затирают друг друга.
INSTANCE="${1:-${INSTANCE:-}}"
if [ -n "$INSTANCE" ]; then
  case "$INSTANCE" in
    *[!A-Za-z0-9_-]*)
      printf '%s\n' "Имя инстанса может содержать только буквы, цифры, '-' и '_': $INSTANCE" >&2
      exit 1
      ;;
  esac
  SERVER_KEY="postgres-mcp-$INSTANCE"
else
  SERVER_KEY="postgres-mcp"
fi
UV_INSTALLER="https://astral.sh/uv/install.sh"
VERSIONS_RAW_URL="https://raw.githubusercontent.com/ShDA009/mcp/master/mcp-versions.txt"
# Fallback-версия, если mcp-versions.txt никогда не удастся скачать (первый запуск
# без сети). Подтягивается лаунчером из репо при каждом старте — актуальную
# версию сотрудники получают без переустановки, см. install/README.md.
FALLBACK_POSTGRES_SPEC="postgres-mcp>=0.3,<0.4"
# Версия Python для запуска сервера. Пин нужен из-за pglast==7.2.0: у неё есть
# готовые колёса только до cp313, и на системе с Python 3.14 uvx иначе уходит в
# сборку из исходников и падает. postgres-mcp требует >=3.12.
PYTHON_PIN="3.13"
# Верхняя граница для MCP SDK: upstream объявил "mcp[cli]>=1.5.0" без верхней
# границы, но в mcp 2.0 удалён модуль mcp.server.fastmcp — сервер падает с
# ModuleNotFoundError на импорте. Держим SDK на ветке 1.x.
MCP_SDK_CONSTRAINT="mcp[cli]<2"
# Корп-модель вместо api.openai.com для LLM-фичи (index tuning).
# Дефолта нет намеренно: upstream жёстко запрашивает модель с именем "gpt-4o"
# (postgres_mcp/index/llm_opt.py), переменной окружения для него нет. Если
# шлюз не обслуживает это имя, он вернёт "No matching route found". Пока имя
# не смаплено на стороне шлюза, шаг имеет смысл пропускать (Enter).
DEFAULT_OPENAI_BASE_URL=""
DEFAULT_OPENAI_API_KEY=""

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

info "== Установка $SERVER_KEY для $PLATFORM =="

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
CONF_DIR="$HOME/.config/$SERVER_KEY"
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

HAVE_DB_URI="no"; env_get DATABASE_URI >/dev/null 2>&1 && HAVE_DB_URI="yes"
CUR_OPENAI_BASE_URL="$(env_get OPENAI_BASE_URL || true)"
CUR_ACCESS_MODE="$(env_get PGMCP_ACCESS_MODE || true)"

CHANGE="yes"
if [ -f "$ENV_FILE" ] && [ "$HAVE_DB_URI" = "yes" ]; then
  info "Найден существующий конфиг: $ENV_FILE"
  printf '  DATABASE_URI      = %s\n' '******** (сохранён)'
  printf '  OPENAI_BASE_URL   = %s\n' "${CUR_OPENAI_BASE_URL:-<не задан>}"
  printf '  Режим доступа     = %s\n' "${CUR_ACCESS_MODE:-restricted}"
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
  info "Введите параметры подключения к PostgreSQL:"
  # Строка подключения содержит пароль целиком — читаем скрытым вводом.
  while :; do
    printf '  DATABASE_URI (postgresql://user:pass@host:5432/db, ввод скрыт): '
    if ! read -rs DATABASE_URI; then
      printf '\n'; die "Ввод прерван (нет данных). Настройка не завершена."
    fi
    printf '\n'
    if [ -z "$DATABASE_URI" ]; then
      warn "Значение не может быть пустым."
      continue
    fi
    case "$DATABASE_URI" in
      postgresql://*|postgres://*) break ;;
      *) warn "Строка должна начинаться с postgresql:// или postgres://." ;;
    esac
  done

  info "Режим доступа сервера к БД:"
  printf '%s\n' "  1) restricted   — только чтение, read-only транзакции (рекомендуется)"
  printf '%s\n' "  2) unrestricted — разрешены запись и DDL"
  printf '%s' "Выберите (1/2, по умолчанию 1): "
  read -r mode_ans
  case "$mode_ans" in
    2) ACCESS_MODE="unrestricted" ;;
    *) ACCESS_MODE="restricted" ;;
  esac

  # LLM-фича необязательна: без неё работает основная (детерминированная)
  # стратегия подбора индексов, отключается только альтернативная — LLM-овая.
  # Пустой ввод = не настраивать, ключ в .env не попадёт вовсе.
  info "LLM-фича (альтернативная стратегия index tuning) — необязательна."
  printf '%s\n' "  Без неё основной подбор индексов работает; выключается только LLM-вариант."
  printf '%s\n' "  Upstream жёстко запрашивает модель с именем 'gpt-4o'. Указывайте адрес,"
  printf '%s\n' "  только если ваш шлюз отдаёт модель под этим именем — иначе он ответит"
  printf '%s\n' "  'No matching route found', и фича всё равно работать не будет."
  printf '%s\n' "  Enter — пропустить (рекомендуется)."
  DEF_BASE="${CUR_OPENAI_BASE_URL:-$DEFAULT_OPENAI_BASE_URL}"
  if [ -n "$DEF_BASE" ]; then
    printf '  OPENAI_BASE_URL [%s]: ' "$DEF_BASE"
  else
    printf '  OPENAI_BASE_URL: '
  fi
  read -r OPENAI_BASE_URL || OPENAI_BASE_URL=""
  [ -z "$OPENAI_BASE_URL" ] && OPENAI_BASE_URL="$DEF_BASE"
  if [ -n "$OPENAI_BASE_URL" ]; then
    # Корп-шлюзы обычно требуют настоящий ключ (наш отвечает 401
    # missing_api_key), поэтому плейсхолдер здесь не подставляем.
    while :; do
      printf '  OPENAI_API_KEY (ввод скрыт): '
      if ! read -rs OPENAI_API_KEY; then
        printf '\n'; die "Ввод прерван (нет данных). Настройка не завершена."
      fi
      printf '\n'
      [ -n "$OPENAI_API_KEY" ] && break
      warn "Ключ не может быть пустым (или нажмите Ctrl+C и пропустите шаг с адресом)."
    done
  else
    OPENAI_API_KEY=""
    ok "  LLM-фича не настроена (основной подбор индексов работает)."
  fi
else
  DATABASE_URI="$(env_get DATABASE_URI || true)"
  ACCESS_MODE="${CUR_ACCESS_MODE:-restricted}"
  OPENAI_BASE_URL="${CUR_OPENAI_BASE_URL:-}"
  OPENAI_API_KEY="$(env_get OPENAI_API_KEY || true)"
  ok "Настройки оставлены без изменений."
fi

# --- 4. Записать .env (атомарно, права 600) ---------------------------------
umask 177
TMP_ENV="$(mktemp "${CONF_DIR}/.env.XXXXXX")"
{
  printf 'DATABASE_URI=%s\n'      "$DATABASE_URI"
  # Читается лаунчером, а не сервером: сервер получает режим флагом --access-mode.
  printf 'PGMCP_ACCESS_MODE=%s\n' "$ACCESS_MODE"
  # postgres-mcp создаёт клиент как OpenAI() без аргументов — openai-SDK берёт
  # base_url и api_key из окружения сам, так LLM-запросы идут в корп-модель.
  # Обе переменные опциональны: без них не работает только LLM-вариант подбора
  # индексов, основной остаётся на месте.
  if [ -n "$OPENAI_BASE_URL" ]; then
    printf 'OPENAI_BASE_URL=%s\n' "$OPENAI_BASE_URL"
    printf 'OPENAI_API_KEY=%s\n'  "$OPENAI_API_KEY"
  fi
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
FALLBACK_SPEC="$FALLBACK_POSTGRES_SPEC"

# 1) Попробовать обновить кеш версии из репо (короткий таймаут — не вешать старт).
TMP_CACHE="\$(mktemp "\${CONF_DIR}/mcp-versions.txt.XXXXXX" 2>/dev/null || true)"
if [ -n "\$TMP_CACHE" ] && curl -sf --max-time 3 -o "\$TMP_CACHE" "\$RAW_URL" 2>/dev/null; then
  mv -f "\$TMP_CACHE" "\$CACHE"
else
  [ -n "\$TMP_CACHE" ] && rm -f "\$TMP_CACHE" 2>/dev/null || true
fi

# 2) Взять POSTGRES_SPEC из кеша, только если строка выглядит как безопасный
#    пакетный спецификатор (буквы/цифры/@/./_/-/,/=/</>/^/~ и слэш для npm-скоупов).
POSTGRES_SPEC="\$FALLBACK_SPEC"
if [ -f "\$CACHE" ]; then
  line="\$(grep -E '^POSTGRES_SPEC=' "\$CACHE" | tail -n1 || true)"
  value="\${line#POSTGRES_SPEC=}"
  value="\${value%\\"}"; value="\${value#\\"}"
  if printf '%s' "\$value" | grep -qE '^[A-Za-z0-9@/._,=<>^~-]+\$'; then
    POSTGRES_SPEC="\$value"
  fi
fi

# 3) Прогрузить .env в окружение процесса: DATABASE_URI и OPENAI_* сервер
#    читает оттуда сам, поэтому в конфиге Cline секретов нет.
set -a
. "\$CONF_DIR/.env"
set +a

# 4) Режим доступа передаётся флагом (переменной окружения для него нет).
ACCESS_MODE="\${PGMCP_ACCESS_MODE:-restricted}"
case "\$ACCESS_MODE" in
  restricted|unrestricted) ;;
  *) ACCESS_MODE="restricted" ;;
esac

# Интерпретатор пинуется явно: зависимость pglast==7.2.0 публикует колёса
# только до cp313, а на машине с Python 3.14 uvx иначе пытается собрать её из
# исходников (нужен make + компилятор) и падает. uv скачает 3.13 сам.
# mcp[cli]<2 — upstream объявил зависимость как ">=1.5.0" без верхней границы,
# а в mcp 2.0 модуль mcp.server.fastmcp удалён, и сервер падает на импорте.
exec "$UVX_BIN" --python "$PYTHON_PIN" --with "$MCP_SDK_CONSTRAINT" --from "\$POSTGRES_SPEC" postgres-mcp --access-mode="\$ACCESS_MODE" "\$@"
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

# Обновляем секцию на месте — не дублируем. Ни версия, ни строка подключения к
# БД НЕ попадают в этот JSON: всё это внутри launch.sh и .env в
# ~/.config/postgres-mcp/.
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
  warn "  - проверьте, что VPN подключён (для доступа к серверу PostgreSQL)."
fi

# --- 8. Итог ----------------------------------------------------------------
echo
ok "== Готово =="
printf '%s\n' "  uv/uvx:       $UVX_BIN"
printf '%s\n' "  Конфиг:       $ENV_FILE"
printf '%s\n' "  Лаунчер:      $LAUNCH_FILE"
printf '%s\n' "  Режим:        $ACCESS_MODE"
printf '%s\n' "  Корп-модель:  ${OPENAI_BASE_URL:-<не настроена, LLM-подбор индексов выключен>}"
printf '%s\n' "  Cline:        $CLINE_CFG (сервер '$SERVER_KEY')"
echo
info "Дальше:"
printf '%s\n' "  1. Полностью перезапустите VS Code (и Cline)."
printf '%s\n' "  2. В Cline проверьте, что MCP-сервер '$SERVER_KEY' активен."
if [ "$PLATFORM" = "macOS" ]; then
  printf '%s\n' "  3. Если PostgreSQL недоступен — убедитесь, что подключён корпоративный VPN."
else
  printf '%s\n' "  3. Если PostgreSQL недоступен — убедитесь, что подключён корпоративный VPN / есть доступ в корп. сеть."
fi
echo
printf '%s\n' "При проблемах обращайтесь к администратору postgres-mcp."
