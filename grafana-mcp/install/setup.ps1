<#
    Установочный скрипт grafana-mcp (Grafana: дашборды, датасорсы, алерты,
    через сторонний сервер grafana/mcp-grafana) для Windows.
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
$ServerKey = 'grafana'
$VersionsRawUrl = 'https://raw.githubusercontent.com/ShDA009/mcp/master/mcp-versions.txt'
# Fallback-версия, если mcp-versions.txt никогда не удастся скачать (первый запуск
# без сети). Лаунчер подтягивает актуальную версию из репо при каждом старте.
$FallbackGrafanaSpec = 'mcp-grafana>=2.0,<2.1'

function Write-Info($m) { Write-Host $m -ForegroundColor Cyan }
function Write-Ok  ($m) { Write-Host $m -ForegroundColor Green }
function Write-Warn2($m){ Write-Host $m -ForegroundColor Yellow }
function Die($m) { Write-Host $m -ForegroundColor Red; Read-Host 'Нажмите Enter для выхода'; exit 1 }

Write-Info '== Установка grafana-mcp для Windows =='

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

$ConfDir = Join-Path $env:USERPROFILE '.grafana-mcp'
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

$curGrafanaUrl   = Get-EnvValue 'GRAFANA_URL'
$haveConfig      = [bool]$curGrafanaUrl -and [bool](Get-EnvValue 'GRAFANA_SERVICE_ACCOUNT_TOKEN')

$change = $true
if ((Test-Path $EnvFile) -and $haveConfig) {
    Write-Info "Найден существующий конфиг: $EnvFile"
    Write-Host "  GRAFANA_URL                    = $curGrafanaUrl"
    Write-Host '  GRAFANA_SERVICE_ACCOUNT_TOKEN  = ******** (сохранён)'
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
    Write-Info 'Введите параметры подключения к Grafana:'
    while ($true) {
        $GrafanaUrl = (Read-NonEmpty '  GRAFANA_URL (например https://grafana.example.com)' $curGrafanaUrl).TrimEnd('/')
        if ($GrafanaUrl -match '^https?://') { break }
        Write-Warn2 'Адрес должен начинаться с https:// или http://.'
    }
    # Токен service account — секрет, читаем скрытым вводом.
    $GrafanaToken = Read-SecretNonEmpty '  GRAFANA_SERVICE_ACCOUNT_TOKEN (ввод скрыт)'
} else {
    $GrafanaUrl   = $curGrafanaUrl
    $GrafanaToken = Get-EnvValue 'GRAFANA_SERVICE_ACCOUNT_TOKEN'
    Write-Ok 'Настройки оставлены без изменений.'
}

# --- 4. Записать .env -------------------------------------------------------
$envLines = @(
    "GRAFANA_URL=$GrafanaUrl"
    "GRAFANA_SERVICE_ACCOUNT_TOKEN=$GrafanaToken"
)
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
    "`$GrafanaSpec = '$FallbackGrafanaSpec'"
    ''
    '# 1) Попробовать обновить кеш версии из репо (короткий таймаут — не вешать старт).'
    'try {'
    '    Invoke-WebRequest -Uri $RawUrl -OutFile "$Cache.new" -TimeoutSec 3 -UseBasicParsing | Out-Null'
    '    Move-Item -Force "$Cache.new" $Cache'
    '} catch {'
    '    Remove-Item -Force "$Cache.new" -ErrorAction SilentlyContinue'
    '}'
    ''
    '# 2) Взять GRAFANA_SPEC из кеша, только если строка похожа на безопасный'
    '#    пакетный спецификатор (буквы/цифры/@/./_/-/,/=/</>/^/~, слэш для скоупов).'
    'if (Test-Path $Cache) {'
    '    $line = Get-Content $Cache | Where-Object { $_ -match ''^GRAFANA_SPEC='' } | Select-Object -Last 1'
    '    if ($line) {'
    '        $value = $line -replace ''^GRAFANA_SPEC='', '''''
    '        $value = $value.Trim(''"'')'
    '        if ($value -match ''^[A-Za-z0-9@/._,=<>^~-]+$'') { $GrafanaSpec = $value }'
    '    }'
    '}'
    ''
    '# 3) Прогрузить переменные из .env в окружение процесса: GRAFANA_URL и'
    '#    GRAFANA_SERVICE_ACCOUNT_TOKEN сервер читает оттуда сам, поэтому в'
    '#    конфиге Cline секретов нет.'
    '$envFile = Join-Path $ConfDir ''.env'''
    'Get-Content $envFile | ForEach-Object {'
    '    if ($_ -match ''^([^=]+)=(.*)$'') {'
    '        [System.Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], ''Process'')'
    '    }'
    '}'
    ''
    '# --tls-skip-verify: корпоративные самоподписанные сертификаты.'
    "& '$UvxBin' --from `$GrafanaSpec mcp-grafana --tls-skip-verify @args"
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
    Write-Warn2 '  - VPN не подключён (для доступа к Grafana).'
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

# Ни версия, ни токен НЕ попадают в этот JSON: всё внутри
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
Write-Host "  Grafana:      $GrafanaUrl"
Write-Host "  Cline:        $ClineCfg (сервер '$ServerKey')"
Write-Host ''
Write-Info 'Дальше:'
Write-Host '  1. Полностью перезапустите VS Code (и Cline).'
Write-Host "  2. В Cline проверьте, что MCP-сервер '$ServerKey' активен."
Write-Host '  3. Если Grafana недоступна — убедитесь, что подключён корпоративный VPN.'
Write-Host ''
Write-Host 'При проблемах обращайтесь к администратору grafana-mcp.'
Read-Host 'Нажмите Enter для выхода'
