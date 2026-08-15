<#
    Установочный скрипт postgres-mcp (PostgreSQL: анализ, тюнинг, explain-планы,
    через сторонний сервер crystaldba/postgres-mcp) для Windows.
    Запуск: правый клик -> "Выполнить с помощью PowerShell",
            либо:  powershell -ExecutionPolicy Bypass -File setup.ps1
    Прав администратора не требует.
#>

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# uv/uvx по умолчанию использует свой набор корневых сертификатов и не доверяет
# корпоративному CA при TLS-инспекции (Zscaler и т.п.) — скачивание падает с
# "invalid peer certificate: UnknownIssuer" / "cannot decrypt peer's message".
# Эти флаги заставляют uv брать сертификаты из системного хранилища Windows,
# куда корп-CA уже добавлен. UV_SYSTEM_CERTS — актуальное имя, UV_NATIVE_TLS —
# для старых версий uv (оба безвредны, если инспекции нет).
$env:UV_SYSTEM_CERTS = '1'
$env:UV_NATIVE_TLS = '1'

# --- Константы --------------------------------------------------------------
$ServerKey = 'postgres-mcp'
$VersionsRawUrl = 'https://raw.githubusercontent.com/ShDA009/mcp/master/mcp-versions.txt'
# Fallback-версия, если mcp-versions.txt никогда не удастся скачать (первый запуск
# без сети). Лаунчер подтягивает актуальную версию из репо при каждом старте.
$FallbackPostgresSpec = 'postgres-mcp>=0.3,<0.4'
# Версия Python для запуска сервера. Пин нужен из-за pglast==7.2.0: у неё есть
# готовые колёса только до cp313, и на системе с Python 3.14 uvx иначе уходит в
# сборку из исходников и падает. postgres-mcp требует >=3.12.
$PythonPin = '3.13'
# Верхняя граница для MCP SDK: upstream объявил "mcp[cli]>=1.5.0" без верхней
# границы, но в mcp 2.0 удалён модуль mcp.server.fastmcp — сервер падает с
# ModuleNotFoundError на импорте. Держим SDK на ветке 1.x.
$McpSdkConstraint = 'mcp[cli]<2'
# Корп-модель вместо api.openai.com для LLM-фичи (index tuning).
# Дефолта нет намеренно: upstream жёстко запрашивает модель с именем "gpt-4o"
# (postgres_mcp/index/llm_opt.py), переменной окружения для него нет. Если
# шлюз не обслуживает это имя, он вернёт "No matching route found". Пока имя
# не смаплено на стороне шлюза, шаг имеет смысл пропускать (Enter).
$DefaultOpenAiBaseUrl = ''

function Write-Info($m) { Write-Host $m -ForegroundColor Cyan }
function Write-Ok  ($m) { Write-Host $m -ForegroundColor Green }
function Write-Warn2($m){ Write-Host $m -ForegroundColor Yellow }
function Die($m) { Write-Host $m -ForegroundColor Red; Read-Host 'Нажмите Enter для выхода'; exit 1 }

Write-Info '== Установка postgres-mcp для Windows =='

# --- 1. Поиск uv/uvx --------------------------------------------------------
function Find-Uvx {
    $cand = Join-Path $env:USERPROFILE '.local\bin\uvx.exe'
    if (Test-Path $cand) { return $cand }
    $cmd = Get-Command uvx -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) {
        $uvxNear = Join-Path (Split-Path $cmd.Source) 'uvx.exe'
        if (Test-Path $uvxNear) { return $uvxNear }
    }
    return $null
}

$UvxBin = Find-Uvx
if ($UvxBin) {
    Write-Ok "uvx найден: $UvxBin"
} else {
    Write-Warn2 'uv не найден ни в %USERPROFILE%\.local\bin, ни в PATH.'
    $ans = Read-Host 'Установить uv в пользовательский профиль (прав администратора не требует)? (y/n)'
    if ($ans -notmatch '^(y|yes)$') {
        Die 'Без uv дальнейшая настройка невозможна. Ничего не изменено. Запустите скрипт снова, когда будете готовы установить uv.'
    }
    Write-Info 'Устанавливаю uv...'
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    } catch {
        Die "Не удалось установить uv. Проверьте доступ в интернет / прокси и повторите.`n$($_.Exception.Message)"
    }
    $UvxBin = Find-Uvx
    if (-not $UvxBin) {
        Die 'uv установлен, но uvx не найден автоматически. Перезапустите PowerShell и запустите скрипт снова.'
    }
    Write-Ok "uv установлен: $UvxBin"
}

# --- 2. Пути конфигов -------------------------------------------------------
# Cline хранит конфиг в двух разных местах в зависимости от версии/сборки:
#   1) %USERPROFILE%\.cline\data\settings\  — свежие версии (standalone-каталог);
#   2) %APPDATA%\Code\...globalStorage\...  — прежний путь внутри расширения.
# Пишем в тот, который реально существует, иначе сервер не появится в списке
# MCP. Если есть оба — берём более свежий по времени изменения.
$ClineNewDir = Join-Path $env:USERPROFILE '.cline\data\settings'
$ClineVsCodeDir = Join-Path $env:APPDATA 'Code\User\globalStorage\saoudrizwan.claude-dev\settings'
$NewCfg = Join-Path $ClineNewDir 'cline_mcp_settings.json'
$VsCodeCfg = Join-Path $ClineVsCodeDir 'cline_mcp_settings.json'

if ((Test-Path $NewCfg) -and (Test-Path $VsCodeCfg)) {
    $newTime = (Get-Item $NewCfg).LastWriteTime
    $oldTime = (Get-Item $VsCodeCfg).LastWriteTime
    $ClineDir = if ($newTime -gt $oldTime) { $ClineNewDir } else { $ClineVsCodeDir }
} elseif (Test-Path $NewCfg) {
    $ClineDir = $ClineNewDir
} elseif (Test-Path $VsCodeCfg) {
    $ClineDir = $ClineVsCodeDir
} else {
    $ClineDir = $ClineNewDir
}
$ClineCfg = Join-Path $ClineDir 'cline_mcp_settings.json'
Write-Ok "Конфиг Cline: $ClineCfg"

$ConfDir = Join-Path $env:USERPROFILE '.postgres-mcp'
$EnvFile = Join-Path $ConfDir '.env'
$LaunchScript = Join-Path $ConfDir 'launch.ps1'
$LaunchFile = Join-Path $ConfDir 'launch.cmd'
if (-not (Test-Path $ConfDir)) { New-Item -ItemType Directory -Path $ConfDir -Force | Out-Null }

# --- 3. Прочитать существующий .env -----------------------------------------
function Get-EnvValue($key) {
    if (-not (Test-Path $EnvFile)) { return $null }
    $line = Select-String -Path $EnvFile -Pattern "^$([regex]::Escape($key))=" -ErrorAction SilentlyContinue |
            Select-Object -Last 1
    if ($line) { return ($line.Line -replace "^$([regex]::Escape($key))=", '') }
    return $null
}

$haveDbUri        = [bool](Get-EnvValue 'DATABASE_URI')
$curOpenAiBaseUrl = Get-EnvValue 'OPENAI_BASE_URL'
$curAccessMode    = Get-EnvValue 'PGMCP_ACCESS_MODE'

$change = $true
if ((Test-Path $EnvFile) -and $haveDbUri) {
    Write-Info "Найден существующий конфиг: $EnvFile"
    Write-Host '  DATABASE_URI      = ******** (сохранён)'
    Write-Host ("  OPENAI_BASE_URL   = {0}" -f ($(if($curOpenAiBaseUrl){$curOpenAiBaseUrl}else{'<не задан>'})))
    Write-Host ("  Режим доступа     = {0}" -f ($(if($curAccessMode){$curAccessMode}else{'restricted'})))
    $ans = Read-Host 'Изменить настройки? (y/n, по умолчанию n)'
    if ($ans -notmatch '^(y|yes)$') { $change = $false }
}

function Read-NonEmpty($prompt, $default) {
    while ($true) {
        if ($default) { $p = "$prompt [$default]" } else { $p = $prompt }
        $v = Read-Host $p
        if ([string]::IsNullOrWhiteSpace($v) -and $default) { $v = $default }
        if (-not [string]::IsNullOrWhiteSpace($v)) { return $v }
        Write-Warn2 'Значение не может быть пустым.'
    }
}

function Read-SecretNonEmpty($prompt) {
    while ($true) {
        $sec = Read-Host $prompt -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
        try   { $v = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        if (-not [string]::IsNullOrWhiteSpace($v)) { return $v }
        Write-Warn2 'Значение не может быть пустым.'
    }
}

if ($change) {
    Write-Info 'Введите параметры подключения к PostgreSQL:'
    # Строка подключения содержит пароль целиком — читаем скрытым вводом.
    while ($true) {
        $DatabaseUri = Read-SecretNonEmpty '  DATABASE_URI (postgresql://user:pass@host:5432/db, ввод скрыт)'
        if ($DatabaseUri -match '^postgres(ql)?://') { break }
        Write-Warn2 'Строка должна начинаться с postgresql:// или postgres://.'
    }

    Write-Info 'Режим доступа сервера к БД:'
    Write-Host '  1) restricted   — только чтение, read-only транзакции (рекомендуется)'
    Write-Host '  2) unrestricted — разрешены запись и DDL'
    $modeAns = Read-Host 'Выберите (1/2, по умолчанию 1)'
    if ($modeAns -eq '2') { $AccessMode = 'unrestricted' } else { $AccessMode = 'restricted' }

    # LLM-фича необязательна: без неё работает основная (детерминированная)
    # стратегия подбора индексов, отключается только альтернативная — LLM-овая.
    # Пустой ввод = не настраивать, ключ в .env не попадёт вовсе.
    Write-Info 'LLM-фича (альтернативная стратегия index tuning) — необязательна.'
    Write-Host '  Без неё основной подбор индексов работает; выключается только LLM-вариант.'
    Write-Host "  Upstream жёстко запрашивает модель с именем 'gpt-4o'. Указывайте адрес,"
    Write-Host "  только если ваш шлюз отдаёт модель под этим именем — иначе он ответит"
    Write-Host "  'No matching route found', и фича всё равно работать не будет."
    Write-Host '  Enter — пропустить (рекомендуется).'
    $defBase = if ($curOpenAiBaseUrl) { $curOpenAiBaseUrl } else { $DefaultOpenAiBaseUrl }
    $prompt = if ($defBase) { "  OPENAI_BASE_URL [$defBase]" } else { '  OPENAI_BASE_URL' }
    $OpenAiBaseUrl = Read-Host $prompt
    if (-not $OpenAiBaseUrl) { $OpenAiBaseUrl = $defBase }
    if ($OpenAiBaseUrl) {
        # Корп-шлюзы обычно требуют настоящий ключ (наш отвечает 401
        # missing_api_key), поэтому плейсхолдер здесь не подставляем.
        $OpenAiApiKey = Read-SecretNonEmpty '  OPENAI_API_KEY (ввод скрыт)'
    } else {
        $OpenAiApiKey = ''
        Write-Ok '  LLM-фича не настроена (основной подбор индексов работает).'
    }
} else {
    $DatabaseUri   = Get-EnvValue 'DATABASE_URI'
    $AccessMode    = if ($curAccessMode) { $curAccessMode } else { 'restricted' }
    $OpenAiBaseUrl = $curOpenAiBaseUrl
    $OpenAiApiKey  = Get-EnvValue 'OPENAI_API_KEY'
    Write-Ok 'Настройки оставлены без изменений.'
}

# --- 4. Записать .env -------------------------------------------------------
# PGMCP_ACCESS_MODE читает лаунчер, а не сервер: серверу режим передаётся
# флагом --access-mode. OPENAI_* сервер подхватывает сам — он создаёт клиент
# как OpenAI() без аргументов, а openai-SDK берёт base_url/api_key из окружения.
# Обе переменные опциональны: без них не работает только LLM-вариант подбора
# индексов, основной остаётся на месте.
$envLines = @(
    "DATABASE_URI=$DatabaseUri"
    "PGMCP_ACCESS_MODE=$AccessMode"
)
if ($OpenAiBaseUrl) {
    $envLines += "OPENAI_BASE_URL=$OpenAiBaseUrl"
    $envLines += "OPENAI_API_KEY=$OpenAiApiKey"
}
$envLines += 'PYTHONUNBUFFERED=1'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($EnvFile, ($envLines -join "`n") + "`n", $utf8)

$icaclsUser = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
icacls $EnvFile /inheritance:r /grant:r "$($icaclsUser):(R,W)" | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warn2 "Не удалось ужесточить права на $EnvFile (icacls вернул код $LASTEXITCODE, продолжаю)."
}
Write-Ok "Настройки сохранены в $EnvFile"

# --- 5. Сгенерировать лаунчер ------------------------------------------------
# Лаунчер подтягивает версию из mcp-versions.txt в репо при каждом старте — так
# обновление версии доезжает до сотрудника без переустановки. Из сети
# берётся ТОЛЬКО строка со спецификатором пакета; перед использованием она
# проверяется регуляркой на допустимые символы, иначе берётся fallback.
# launch.ps1 — сама логика (PowerShell, легко читать/поддерживать).
# launch.cmd — тонкая обёртка поверх launch.ps1, потому что Cline на Windows
# вызывает `command` как исполняемый файл, а не интерпретирует .ps1 напрямую.
$launchScriptLines = @(
    '$ErrorActionPreference = ''Stop'''
    '# см. комментарий в setup.ps1: доверять корп-CA при обновлении пакета.'
    '$env:UV_SYSTEM_CERTS = ''1'''
    '$env:UV_NATIVE_TLS = ''1'''
    "`$ConfDir = '$ConfDir'"
    "`$Cache = Join-Path `$ConfDir 'mcp-versions.txt'"
    "`$RawUrl = '$VersionsRawUrl'"
    "`$PostgresSpec = '$FallbackPostgresSpec'"
    ''
    '# 1) Попробовать обновить кеш версии из репо (короткий таймаут — не вешать старт).'
    'try {'
    '    Invoke-WebRequest -Uri $RawUrl -OutFile "$Cache.new" -TimeoutSec 3 -UseBasicParsing | Out-Null'
    '    Move-Item -Force "$Cache.new" $Cache'
    '} catch {'
    '    Remove-Item -Force "$Cache.new" -ErrorAction SilentlyContinue'
    '}'
    ''
    '# 2) Взять POSTGRES_SPEC из кеша, только если строка похожа на безопасный'
    '#    пакетный спецификатор (буквы/цифры/@/./_/-/,/=/</>/^/~, слэш для скоупов).'
    'if (Test-Path $Cache) {'
    '    $line = Get-Content $Cache | Where-Object { $_ -match ''^POSTGRES_SPEC='' } | Select-Object -Last 1'
    '    if ($line) {'
    '        $value = $line -replace ''^POSTGRES_SPEC='', '''''
    '        $value = $value.Trim(''"'')'
    '        if ($value -match ''^[A-Za-z0-9@/._,=<>^~-]+$'') { $PostgresSpec = $value }'
    '    }'
    '}'
    ''
    '# 3) Прогрузить .env в окружение процесса: DATABASE_URI и OPENAI_* сервер'
    '#    читает оттуда сам, поэтому в конфиге Cline секретов нет.'
    '$envFile = Join-Path $ConfDir ''.env'''
    'Get-Content $envFile | ForEach-Object {'
    '    if ($_ -match ''^([^=]+)=(.*)$'') {'
    '        [System.Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], ''Process'')'
    '    }'
    '}'
    ''
    '# 4) Режим доступа передаётся флагом (переменной окружения для него нет).'
    '$AccessMode = $env:PGMCP_ACCESS_MODE'
    'if ($AccessMode -ne ''unrestricted'') { $AccessMode = ''restricted'' }'
    ''
    '# Интерпретатор пинуется явно: pglast==7.2.0 публикует колёса только до'
    '# cp313, на Python 3.14 uvx иначе пытается собрать её из исходников и падает.'
    '# mcp[cli]<2 — в mcp 2.0 удалён mcp.server.fastmcp, сервер падает на импорте.'
    "& '$UvxBin' --python '$PythonPin' --with '$McpSdkConstraint' --from `$PostgresSpec postgres-mcp `"--access-mode=`$AccessMode`" @args"
    'exit $LASTEXITCODE'
)
[System.IO.File]::WriteAllText($LaunchScript, ($launchScriptLines -join "`r`n") + "`r`n", $utf8)

$launchCmdLines = @(
    '@echo off'
    "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""$LaunchScript"" %*"
)
[System.IO.File]::WriteAllText($LaunchFile, ($launchCmdLines -join "`r`n") + "`r`n", $utf8)
Write-Ok "Лаунчер сгенерирован: $LaunchFile"

# --- 6. Проверочный вызов ---------------------------------------------------
Write-Info 'Проверяю, что пакет ставится и запускается (launch.cmd --help)...'
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$helpOutput = & $LaunchFile --help 2>&1 | Out-String
$exitCode = $LASTEXITCODE
$ErrorActionPreference = $prevEap
$ok = ($exitCode -eq 0)
if ($ok) {
    Write-Ok 'Проверочный запуск успешен.'
} else {
    Write-Warn2 'Проверочный запуск завершился с ошибкой:'
    Write-Host $helpOutput -ForegroundColor DarkYellow
    Write-Warn2 'Возможные причины:'
    Write-Warn2 '  - нет доступа к github.com и pypi.org (интернет / прокси);'
    Write-Warn2 '  - VPN не подключён (для доступа к серверу PostgreSQL).'
    Die 'Конфиг Cline не изменён — сервер в текущем состоянии не запустится. Устраните причину выше и запустите скрипт снова.'
}

# --- 7. Обновить конфиг Cline идемпотентно ----------------------------------
if (-not (Test-Path $ClineDir)) { New-Item -ItemType Directory -Path $ClineDir -Force | Out-Null }

Write-Info "Обновляю конфиг Cline: $ClineCfg"
$cfg = $null
if (Test-Path $ClineCfg) {
    try {
        $cfg = Get-Content -Raw -Path $ClineCfg | ConvertFrom-Json
    } catch {
        Die "Не удалось разобрать существующий $ClineCfg как JSON — файл не тронут, чтобы не потерять уже настроенные MCP-серверы.`nИсправьте файл вручную и запустите скрипт снова.`n$($_.Exception.Message)"
    }
}
if (-not $cfg) { $cfg = [pscustomobject]@{ mcpServers = [pscustomobject]@{} } }
if (-not ($cfg.PSObject.Properties.Name -contains 'mcpServers') -or $null -eq $cfg.mcpServers) {
    $cfg | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{}) -Force
}

# Ни версия, ни строка подключения к БД НЕ попадают в этот JSON: всё внутри
# launch.cmd и .env.
#
# У Cline две схемы записи сервера, и версии их не понимают взаимно:
#   новая:  transport = @{ type = 'stdio'; command = ...; args = @() }
#   старая: command / args / transportType на верхнем уровне
# Подстраиваемся под то, что уже лежит в файле у соседних серверов.
$useNewSchema = $true
foreach ($p in $cfg.mcpServers.PSObject.Properties) {
    if ($p.Name -eq $ServerKey) { continue }
    $v = $p.Value
    if ($null -eq $v) { continue }
    if ($v.PSObject.Properties.Name -contains 'transport') { $useNewSchema = $true; break }
    if (($v.PSObject.Properties.Name -contains 'transportType') -or
        ($v.PSObject.Properties.Name -contains 'command')) { $useNewSchema = $false; break }
}

if ($useNewSchema) {
    $serverObj = [pscustomobject]@{
        transport = [pscustomobject]@{
            type    = 'stdio'
            command = $LaunchFile
            args    = @()
        }
        disabled  = $false
        timeout   = 60
    }
} else {
    $serverObj = [pscustomobject]@{
        command       = $LaunchFile
        args          = @()
        disabled      = $false
        transportType = 'stdio'
    }
}

# Если сервер уже был в конфиге и его выключили вручную — не включаем обратно.
$prev = $cfg.mcpServers.PSObject.Properties[$ServerKey]
if ($prev -and $prev.Value -and ($prev.Value.PSObject.Properties.Name -contains 'disabled')) {
    $serverObj.disabled = [bool]$prev.Value.disabled
}

$cfg.mcpServers | Add-Member -NotePropertyName $ServerKey -NotePropertyValue $serverObj -Force

if (Test-Path $ClineCfg) {
    $backupPath = "$ClineCfg.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -Path $ClineCfg -Destination $backupPath -Force
    Write-Info "  резервная копия: $backupPath"
}

$json = $cfg | ConvertTo-Json -Depth 32
[System.IO.File]::WriteAllText($ClineCfg, $json + "`n", $utf8)
Write-Ok "  секция '$ServerKey' обновлена (лаунчер $LaunchFile)"

# --- 8. Итог ----------------------------------------------------------------
Write-Host ''
Write-Ok '== Готово =='
Write-Host "  uv/uvx:       $UvxBin"
Write-Host "  Конфиг:       $EnvFile"
Write-Host "  Лаунчер:      $LaunchFile"
Write-Host "  Режим:        $AccessMode"
Write-Host ("  Корп-модель:  {0}" -f ($(if($OpenAiBaseUrl){$OpenAiBaseUrl}else{'<не настроена, LLM-подбор индексов выключен>'})))
Write-Host "  Cline:        $ClineCfg (сервер '$ServerKey')"
Write-Host ''
Write-Info 'Дальше:'
Write-Host '  1. Полностью перезапустите VS Code (и Cline).'
Write-Host "  2. В Cline проверьте, что MCP-сервер '$ServerKey' активен."
Write-Host '  3. Если PostgreSQL недоступен — убедитесь, что подключён корпоративный VPN.'
Write-Host ''
Write-Host 'При проблемах обращайтесь к администратору postgres-mcp.'
Read-Host 'Нажмите Enter для выхода'
