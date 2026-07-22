<#
    Установочный скрипт outlook-mcp (EWS MCP-сервер) для Windows.
    Запуск: правый клик -> "Выполнить с помощью PowerShell",
            либо:  powershell -ExecutionPolicy Bypass -File setup.ps1
    Прав администратора не требует.
#>

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# uv по умолчанию использует свой набор корневых сертификатов и не доверяет
# корпоративному CA при TLS-инспекции (Zscaler и т.п.) — скачивание Python
# и пакетов падает с "invalid peer certificate: UnknownIssuer". Эти флаги
# заставляют uv брать сертификаты из системного хранилища Windows, куда
# корп-CA уже добавлен. UV_SYSTEM_CERTS — актуальное имя, UV_NATIVE_TLS —
# для старых версий uv (оба безвредны, если инспекции нет).
$env:UV_SYSTEM_CERTS = '1'
$env:UV_NATIVE_TLS = '1'

# --- Константы --------------------------------------------------------------
$RepoUrl    = 'https://github.com/ShDA009/mcp.git'
$SubDir     = 'outlook-mcp'
$McpEntry   = 'ews-mcp-server'
$ServerKey  = 'outlook-mcp'
$VersionsRawUrl = 'https://raw.githubusercontent.com/ShDA009/mcp/master/mcp-versions.txt'
# Fallback-ref, если mcp-versions.txt недоступен при самой первой установке.
$FallbackRef = 'master'

function Write-Info($m) { Write-Host $m -ForegroundColor Cyan }
function Write-Ok  ($m) { Write-Host $m -ForegroundColor Green }
function Write-Warn2($m){ Write-Host $m -ForegroundColor Yellow }
function Die($m) { Write-Host $m -ForegroundColor Red; Read-Host 'Нажмите Enter для выхода'; exit 1 }

Write-Info '== Установка outlook-mcp для Windows =='

# --- 1. Поиск uv ------------------------------------------------------------
# Нужен именно uv (не uvx): им создаётся venv и ставится пакет.
function Find-Uv {
    # 1) типичное место установки
    $cand = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
    if (Test-Path $cand) { return $cand }
    # 2) в PATH
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

$UvBin = Find-Uv
if ($UvBin) {
    Write-Ok "uv найден: $UvBin"
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
    $UvBin = Find-Uv
    if (-not $UvBin) {
        Die 'uv установлен, но не найден автоматически. Перезапустите PowerShell и запустите скрипт снова.'
    }
    Write-Ok "uv установлен: $UvBin"
}

# --- 2. Пути конфигов -------------------------------------------------------
$ClineDir = Join-Path $env:APPDATA 'Code\User\globalStorage\saoudrizwan.claude-dev\settings'
$ClineCfg = Join-Path $ClineDir 'cline_mcp_settings.json'

$ConfDir = Join-Path $env:USERPROFILE '.outlook-mcp'
$EnvFile = Join-Path $ConfDir '.env'
$VenvDir = Join-Path $ConfDir 'venv'
$RefFile = Join-Path $ConfDir '.installed-ref'
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

$curUrl   = Get-EnvValue 'EWS_URL'
$curUser  = Get-EnvValue 'EWS_USERNAME'
$curEmail = Get-EnvValue 'EWS_EMAIL'
$curPass  = Get-EnvValue 'EWS_PASSWORD'
$havePass = [bool]$curPass

$change = $true
if ((Test-Path $EnvFile) -and $curUser) {
    Write-Info "Найден существующий конфиг: $EnvFile"
    Write-Host ("  EWS_URL      = {0}" -f ($(if($curUrl){$curUrl}else{'<не задан>'})))
    Write-Host ("  EWS_USERNAME = {0}" -f ($(if($curUser){$curUser}else{'<не задан>'})))
    Write-Host ("  EWS_EMAIL    = {0}" -f ($(if($curEmail){$curEmail}else{'<не задан>'})))
    Write-Host ("  EWS_PASSWORD = {0}" -f ($(if($havePass){'******** (сохранён)'}else{'<не задан>'})))
    $ans = Read-Host 'Изменить креды? (y/n, по умолчанию n)'
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

if ($change) {
    Write-Info 'Введите параметры подключения к Exchange (EWS):'
    $EwsUrl   = Read-NonEmpty '  EWS URL (https://.../EWS/Exchange.asmx)' $curUrl
    $EwsUser  = Read-NonEmpty '  Логин (EWS_USERNAME, без домена)' $curUser
    $EwsEmail = Read-NonEmpty '  Email ящика (EWS_EMAIL, полный SMTP)' $curEmail
    while ($true) {
        $sec = Read-Host '  Пароль (ввод скрыт)' -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
        try   { $EwsPass = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        if (-not [string]::IsNullOrWhiteSpace($EwsPass)) { break }
        Write-Warn2 'Пароль не может быть пустым.'
    }
} else {
    $EwsUrl = $curUrl; $EwsUser = $curUser; $EwsEmail = $curEmail; $EwsPass = $curPass
    Write-Ok 'Креды оставлены без изменений.'
}

# --- 4. Записать .env -------------------------------------------------------
$envLines = @(
    "EWS_URL=$EwsUrl"
    "EWS_USERNAME=$EwsUser"
    "EWS_EMAIL=$EwsEmail"
    "EWS_PASSWORD=$EwsPass"
)
# без BOM, LF (это кодировка выходных данных скрипта, не самого .ps1-файла)
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($EnvFile, ($envLines -join "`n") + "`n", $utf8)

# ограничить доступ к файлу текущим пользователем
$icaclsUser = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
icacls $EnvFile /inheritance:r /grant:r "$($icaclsUser):(R,W)" | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warn2 "Не удалось ужесточить права на $EnvFile (icacls вернул код $LASTEXITCODE, продолжаю)."
}
Write-Ok "Креды сохранены в $EnvFile"

# --- 5. Установить пакет в локальный venv -----------------------------------
# Раньше в конфиг Cline писался `uvx --from git+... ews-mcp-server`, то есть
# резолв зависимостей из git выполнялся при КАЖДОМ старте сервера. Под Cline
# это давало "Failed to resolve --with requirement" / Connection closed.
# Теперь пакет ставится один раз сюда, а Cline запускает готовый лаунчер.

# Ref берём из mcp-versions.txt (как и остальные серверы), с fallback.
$TargetRef = $FallbackRef
try {
    $versionsText = (Invoke-WebRequest -Uri $VersionsRawUrl -TimeoutSec 10 -UseBasicParsing).Content
    $refLine = $versionsText -split "`n" | Where-Object { $_ -match '^OUTLOOK_REF=' } | Select-Object -Last 1
    if ($refLine) {
        $refValue = ($refLine -replace '^OUTLOOK_REF=', '').Trim().Trim('"')
        if ($refValue -match '^[A-Za-z0-9._/-]+$') { $TargetRef = $refValue }
    }
} catch {
    Write-Warn2 "Не удалось получить mcp-versions.txt, использую '$FallbackRef'."
}

$PkgSpec = "git+$RepoUrl@$TargetRef#subdirectory=$SubDir"
Write-Info "Устанавливаю пакет в $VenvDir (ref: $TargetRef)..."
Write-Info 'Первая установка занимает 1-3 минуты (скачивание Python и зависимостей).'

$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$venvLog = & $UvBin venv $VenvDir --python 3.11 2>&1 | Out-String
$venvCode = $LASTEXITCODE
$ErrorActionPreference = $prevEap
if ($venvCode -ne 0) {
    Write-Warn2 'Не удалось создать venv:'
    Write-Host $venvLog -ForegroundColor DarkYellow
    Die 'Конфиг Cline не изменён. Устраните причину выше и запустите скрипт снова.'
}

$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$pipLog = & $UvBin pip install --python $VenvDir $PkgSpec 2>&1 | Out-String
$pipCode = $LASTEXITCODE
$ErrorActionPreference = $prevEap
if ($pipCode -ne 0) {
    Write-Warn2 'Не удалось установить пакет:'
    Write-Host $pipLog -ForegroundColor DarkYellow
    Write-Warn2 'Возможные причины:'
    Write-Warn2 '  - нет доступа к github.com / pypi.org (интернет / прокси);'
    Write-Warn2 '  - конфликт версий зависимостей.'
    Die 'Конфиг Cline не изменён. Устраните причину выше и запустите скрипт снова.'
}

$ServerExe = Join-Path $VenvDir "Scripts\$McpEntry.exe"
if (-not (Test-Path $ServerExe)) {
    Die "Пакет установлен, но $ServerExe не найден. Возможно, изменился entry point в pyproject.toml."
}
[System.IO.File]::WriteAllText($RefFile, $TargetRef + "`n", $utf8)
Write-Ok "Пакет установлен: $ServerExe"

# --- 6. Сгенерировать лаунчер с самообновлением -----------------------------
# Лаунчер при старте сверяет OUTLOOK_REF из репо с .installed-ref и
# переустанавливает пакет ТОЛЬКО при расхождении. Обычный старт = запуск exe,
# без резолва зависимостей и без обращения к git.
# launch.cmd — тонкая обёртка: Cline вызывает `command` как исполняемый файл
# и не интерпретирует .ps1 напрямую.
$launchScriptLines = @(
    '$ErrorActionPreference = ''Stop'''
    '# см. комментарий в setup.ps1: доверять корп-CA при обновлении пакета.'
    '$env:UV_SYSTEM_CERTS = ''1'''
    '$env:UV_NATIVE_TLS = ''1'''
    "`$ConfDir = '$ConfDir'"
    "`$VenvDir = '$VenvDir'"
    "`$RefFile = '$RefFile'"
    "`$RawUrl = '$VersionsRawUrl'"
    "`$RepoUrl = '$RepoUrl'"
    "`$SubDir = '$SubDir'"
    "`$UvBin = '$UvBin'"
    "`$ServerExe = '$ServerExe'"
    ''
    '# 1) Узнать целевой ref (короткий таймаут — не вешать старт сервера).'
    '$targetRef = $null'
    'try {'
    '    $text = (Invoke-WebRequest -Uri $RawUrl -TimeoutSec 3 -UseBasicParsing).Content'
    '    $line = $text -split "`n" | Where-Object { $_ -match ''^OUTLOOK_REF='' } | Select-Object -Last 1'
    '    if ($line) {'
    '        $v = ($line -replace ''^OUTLOOK_REF='', '''').Trim().Trim(''"'')'
    '        if ($v -match ''^[A-Za-z0-9._/-]+$'') { $targetRef = $v }'
    '    }'
    '} catch { }'
    ''
    '# 2) Переустановить пакет, только если ref изменился или venv пропал.'
    '$installedRef = if (Test-Path $RefFile) { (Get-Content $RefFile -Raw).Trim() } else { $null }'
    'if ($targetRef -and (($targetRef -ne $installedRef) -or (-not (Test-Path $ServerExe)))) {'
    '    try {'
    '        if (-not (Test-Path $VenvDir)) { & $UvBin venv $VenvDir --python 3.11 | Out-Null }'
    '        & $UvBin pip install --python $VenvDir "git+$RepoUrl@$targetRef#subdirectory=$SubDir" | Out-Null'
    '        if ($LASTEXITCODE -eq 0) { Set-Content -Path $RefFile -Value $targetRef -NoNewline }'
    '    } catch { }'
    '}'
    ''
    '# 3) Запустить сервер. Креды сервер читает сам из .env (см. config.py).'
    '& $ServerExe @args'
    'exit $LASTEXITCODE'
)
[System.IO.File]::WriteAllText($LaunchScript, ($launchScriptLines -join "`r`n") + "`r`n", $utf8)

$launchCmdLines = @(
    '@echo off'
    "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""$LaunchScript"" %*"
)
[System.IO.File]::WriteAllText($LaunchFile, ($launchCmdLines -join "`r`n") + "`r`n", $utf8)
Write-Ok "Лаунчер сгенерирован: $LaunchFile"

# --- 7. Проверочный вызов ---------------------------------------------------
Write-Info 'Проверяю, что сервер запускается (--help)...'
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$helpOutput = & $ServerExe --help 2>&1 | Out-String
$exitCode = $LASTEXITCODE
$ErrorActionPreference = $prevEap
if ($exitCode -eq 0) {
    Write-Ok 'Проверочный запуск успешен.'
} else {
    Write-Warn2 'Проверочный запуск завершился с ошибкой:'
    Write-Host $helpOutput -ForegroundColor DarkYellow
    Die 'Конфиг Cline не изменён. Устраните причину выше и запустите скрипт снова.'
}

# --- 8. Обновить конфиг Cline идемпотентно ----------------------------------
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

# Ни креды, ни версия НЕ попадают в этот JSON: креды outlook_mcp/config.py
# читает сам из %USERPROFILE%\.outlook-mcp\.env, версию держит лаунчер.
$serverObj = [pscustomobject]@{
    command       = $LaunchFile
    args          = @()
    disabled      = $false
    transportType = 'stdio'
}

# Обновляем секцию на месте (Add-Member -Force перезаписывает существующую, не дублируя).
$cfg.mcpServers | Add-Member -NotePropertyName $ServerKey -NotePropertyValue $serverObj -Force

if (Test-Path $ClineCfg) {
    $backupPath = "$ClineCfg.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -Path $ClineCfg -Destination $backupPath -Force
    Write-Info "  резервная копия: $backupPath"
}

$json = $cfg | ConvertTo-Json -Depth 32
[System.IO.File]::WriteAllText($ClineCfg, $json + "`n", $utf8)
Write-Ok "  секция '$ServerKey' обновлена (лаунчер $LaunchFile)"

# --- 9. Итог ----------------------------------------------------------------
Write-Host ''
Write-Ok '== Готово =='
Write-Host "  uv:       $UvBin"
Write-Host "  Пакет:    $VenvDir (ref: $TargetRef)"
Write-Host "  Конфиг:   $EnvFile"
Write-Host "  Лаунчер:  $LaunchFile"
Write-Host "  Cline:    $ClineCfg (сервер '$ServerKey')"
Write-Host ''
Write-Info 'Дальше:'
Write-Host '  1. Полностью перезапустите VS Code (и Cline).'
Write-Host "  2. В Cline проверьте, что MCP-сервер '$ServerKey' активен."
Write-Host '  3. Если EWS недоступен — убедитесь, что подключён корпоративный VPN.'
Write-Host ''
Write-Host 'При проблемах обращайтесь к администратору outlook-mcp.'
Read-Host 'Нажмите Enter для выхода'
