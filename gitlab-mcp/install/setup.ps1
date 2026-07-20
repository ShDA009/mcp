<#
    Установочный скрипт gitlab-mcp (GitLab через сторонний сервер
    zereight/gitlab-mcp, Node/npm) для Windows.
    Запуск: правый клик -> "Выполнить с помощью PowerShell",
            либо:  powershell -ExecutionPolicy Bypass -File setup.ps1
    Прав администратора не требует.
#>

$ErrorActionPreference = 'Stop'

# --- Константы --------------------------------------------------------------
$ServerKey = 'gitlab-mcp'
$VersionsRawUrl = 'https://raw.githubusercontent.com/ShDA009/mcp/master/mcp-versions.txt'
# Fallback-версия, если mcp-versions.txt никогда не удастся скачать (первый запуск
# без сети). Лаунчер подтягивает актуальную версию из репо при каждом старте.
$FallbackGitlabSpec = '@zereight/mcp-gitlab@^2.1'

function Write-Info($m) { Write-Host $m -ForegroundColor Cyan }
function Write-Ok  ($m) { Write-Host $m -ForegroundColor Green }
function Write-Warn2($m){ Write-Host $m -ForegroundColor Yellow }
function Die($m) { Write-Host $m -ForegroundColor Red; Read-Host 'Нажмите Enter для выхода'; exit 1 }

Write-Info '== Установка gitlab-mcp для Windows =='

# --- Проверка Node.js/npx ----------------------------------------------------
# В отличие от остальных серверов в репозитории, этот написан на Node — 'uv'
# его не ставит, нужен отдельно установленный Node.js >= 18.
$npxCmd = Get-Command npx -ErrorAction SilentlyContinue
$nodeCmd = Get-Command node -ErrorAction SilentlyContinue
if (-not $npxCmd -or -not $nodeCmd) {
    Die "Не найден Node.js/npx. Установите Node.js (https://nodejs.org, версия >= 18) и запустите скрипт снова."
}
Write-Ok "node: $(& node --version), npx: $(& npx --version)"

# --- 1. Пути конфигов -------------------------------------------------------
$ClineDir = Join-Path $env:APPDATA 'Code\User\globalStorage\saoudrizwan.claude-dev\settings'
$ClineCfg = Join-Path $ClineDir 'cline_mcp_settings.json'

$ConfDir = Join-Path $env:USERPROFILE '.gitlab-mcp'
$EnvFile = Join-Path $ConfDir '.env'
$LaunchScript = Join-Path $ConfDir 'launch.ps1'
$LaunchFile = Join-Path $ConfDir 'launch.cmd'
if (-not (Test-Path $ConfDir)) { New-Item -ItemType Directory -Path $ConfDir -Force | Out-Null }

# --- 2. Прочитать существующий .env -----------------------------------------
function Get-EnvValue($key) {
    if (-not (Test-Path $EnvFile)) { return $null }
    $line = Select-String -Path $EnvFile -Pattern "^$([regex]::Escape($key))=" -ErrorAction SilentlyContinue |
            Select-Object -Last 1
    if ($line) { return ($line.Line -replace "^$([regex]::Escape($key))=", '') }
    return $null
}

$curApiUrl = Get-EnvValue 'GITLAB_API_URL'
$haveToken = [bool](Get-EnvValue 'GITLAB_PERSONAL_ACCESS_TOKEN')

$change = $true
if ((Test-Path $EnvFile) -and $curApiUrl) {
    Write-Info "Найден существующий конфиг: $EnvFile"
    Write-Host ("  GITLAB_API_URL                = {0}" -f ($(if($curApiUrl){$curApiUrl}else{'<не задан>'})))
    Write-Host ("  GITLAB_PERSONAL_ACCESS_TOKEN  = {0}" -f ($(if($haveToken){'******** (сохранён)'}else{'<не задан>'})))
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

function Read-SecretNonEmpty($prompt) {
    while ($true) {
        $sec = Read-Host $prompt -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
        try   { $v = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        if (-not [string]::IsNullOrWhiteSpace($v)) { return $v }
        Write-Warn2 'Токен не может быть пустым.'
    }
}

if ($change) {
    Write-Info 'Введите параметры подключения к GitLab:'
    $GitlabApiUrl = Read-NonEmpty '  GitLab API URL (https://gitlab.example.com/api/v4)' $curApiUrl
    $GitlabToken  = Read-SecretNonEmpty '  Personal Access Token (ввод скрыт)'
} else {
    $GitlabApiUrl = $curApiUrl
    $GitlabToken  = Get-EnvValue 'GITLAB_PERSONAL_ACCESS_TOKEN'
    Write-Ok 'Креды оставлены без изменений.'
}

# --- 3. Записать .env --------------------------------------------------------
# GITLAB_PERMISSION_MODE=full — эквивалент прежнего GITLAB_READ_ONLY_MODE=false
# (upstream 2.1.x объявил READ_ONLY_MODE устаревшим в пользу PERMISSION_MODE).
$envLines = @(
    "GITLAB_API_URL=$GitlabApiUrl"
    "GITLAB_PERSONAL_ACCESS_TOKEN=$GitlabToken"
    'GITLAB_PERMISSION_MODE=full'
    'USE_GITLAB_WIKI=true'
    'USE_MILESTONE=true'
    'USE_PIPELINE=true'
    'NODE_TLS_REJECT_UNAUTHORIZED=0'
)
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($EnvFile, ($envLines -join "`n") + "`n", $utf8)

try {
    icacls $EnvFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
} catch { Write-Warn2 "Не удалось ужесточить права на $EnvFile (продолжаю)." }
Write-Ok "Креды сохранены в $EnvFile"

# --- 4. Сгенерировать лаунчер ------------------------------------------------
# Лаунчер подтягивает версию из mcp-versions.txt в репо при каждом старте — так
# обновление версии доезжает до сотрудника без переустановки. Из сети
# берётся ТОЛЬКО строка со спецификатором пакета; перед использованием она
# проверяется регуляркой на допустимые символы, иначе берётся fallback.
# launch.ps1 — сама логика. launch.cmd — тонкая обёртка, потому что Cline на
# Windows вызывает `command` как исполняемый файл, не интерпретирует .ps1.
$launchScriptLines = @(
    '$ErrorActionPreference = ''Stop'''
    "`$ConfDir = '$ConfDir'"
    "`$Cache = Join-Path `$ConfDir 'mcp-versions.txt'"
    "`$RawUrl = '$VersionsRawUrl'"
    "`$GitlabSpec = '$FallbackGitlabSpec'"
    ''
    '# 1) Попробовать обновить кеш версии из репо (короткий таймаут — не вешать старт).'
    'try {'
    '    Invoke-WebRequest -Uri $RawUrl -OutFile "$Cache.new" -TimeoutSec 3 -UseBasicParsing | Out-Null'
    '    Move-Item -Force "$Cache.new" $Cache'
    '} catch {'
    '    Remove-Item -Force "$Cache.new" -ErrorAction SilentlyContinue'
    '}'
    ''
    '# 2) Взять GITLAB_SPEC из кеша, только если строка похожа на безопасный'
    '#    пакетный спецификатор (буквы/цифры/@/./_/-/,/=/</>/^/~, слэш для скоупов).'
    'if (Test-Path $Cache) {'
    '    $line = Get-Content $Cache | Where-Object { $_ -match ''^GITLAB_SPEC='' } | Select-Object -Last 1'
    '    if ($line) {'
    '        $value = $line -replace ''^GITLAB_SPEC='', '''''
    '        $value = $value.Trim(''"'')'
    '        if ($value -match ''^[A-Za-z0-9@/._,=<>^~-]+$'') { $GitlabSpec = $value }'
    '    }'
    '}'
    ''
    '# 3) Прогрузить .env в окружение процесса и запустить сервер.'
    '$envFile = Join-Path $ConfDir ''.env'''
    'Get-Content $envFile | ForEach-Object {'
    '    if ($_ -match ''^([^=]+)=(.*)$'') {'
    '        [System.Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], ''Process'')'
    '    }'
    '}'
    ''
    '& npx -y $GitlabSpec @args'
    'exit $LASTEXITCODE'
)
[System.IO.File]::WriteAllText($LaunchScript, ($launchScriptLines -join "`r`n") + "`r`n", $utf8)

$launchCmdLines = @(
    '@echo off'
    "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""$LaunchScript"" %*"
)
[System.IO.File]::WriteAllText($LaunchFile, ($launchCmdLines -join "`r`n") + "`r`n", $utf8)
Write-Ok "Лаунчер сгенерирован: $LaunchFile"

# --- 5. Обновить конфиг Cline идемпотентно ----------------------------------
if (-not (Test-Path $ClineDir)) { New-Item -ItemType Directory -Path $ClineDir -Force | Out-Null }

Write-Info "Обновляю конфиг Cline: $ClineCfg"
$cfg = $null
if (Test-Path $ClineCfg) {
    try { $cfg = Get-Content -Raw -Path $ClineCfg | ConvertFrom-Json } catch { $cfg = $null }
}
if (-not $cfg) { $cfg = [pscustomobject]@{ mcpServers = [pscustomobject]@{} } }
if (-not ($cfg.PSObject.Properties.Name -contains 'mcpServers') -or $null -eq $cfg.mcpServers) {
    $cfg | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{}) -Force
}

# Ни версия, ни креды НЕ попадают в этот JSON: всё внутри launch.cmd и .env.
$serverObj = [pscustomobject]@{
    command       = $LaunchFile
    args          = @()
    disabled      = $false
    transportType = 'stdio'
}

$cfg.mcpServers | Add-Member -NotePropertyName $ServerKey -NotePropertyValue $serverObj -Force

$json = $cfg | ConvertTo-Json -Depth 10
[System.IO.File]::WriteAllText($ClineCfg, $json + "`n", $utf8)
Write-Ok "  секция '$ServerKey' обновлена (лаунчер $LaunchFile)"

# --- 6. Проверочный вызов ---------------------------------------------------
# gitlab-mcp не поддерживает --help и падает без валидных кредов — вместо
# --help запускаем сервер на несколько секунд и ищем в логе подтверждение
# успешного старта, затем завершаем процесс.
Write-Info 'Проверяю, что пакет ставится и запускается...'
$selftestOk = $false
$logFile = [System.IO.Path]::GetTempFileName()
try {
    $proc = Start-Process -FilePath $LaunchFile -RedirectStandardOutput $logFile `
                           -RedirectStandardError "$logFile.err" -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 20
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    $logContent = (Get-Content $logFile -ErrorAction SilentlyContinue) + (Get-Content "$logFile.err" -ErrorAction SilentlyContinue)
    if ($logContent -match '(?i)configuration validation passed|stdio transport') { $selftestOk = $true }
} catch {
    $selftestOk = $false
} finally {
    Remove-Item $logFile, "$logFile.err" -ErrorAction SilentlyContinue
}

if ($selftestOk) {
    Write-Ok 'Проверочный запуск успешен.'
} else {
    Write-Warn2 'Проверочный запуск не подтвердил успешный старт за 20 секунд.'
    Write-Warn2 'Если Cline не подключится:'
    Write-Warn2 '  - проверьте доступ в интернет / к github.com и registry.npmjs.org (прокси);'
    Write-Warn2 '  - проверьте, что VPN подключён (для доступа к GitLab);'
    Write-Warn2 '  - проверьте правильность GITLAB_API_URL и токена.'
}

# --- 7. Итог ----------------------------------------------------------------
Write-Host ''
Write-Ok '== Готово =='
Write-Host "  node/npx: $($nodeCmd.Source)"
Write-Host "  Конфиг:   $EnvFile"
Write-Host "  Лаунчер:  $LaunchFile"
Write-Host "  Cline:    $ClineCfg (сервер '$ServerKey')"
Write-Host ''
Write-Info 'Дальше:'
Write-Host '  1. Полностью перезапустите VS Code (и Cline).'
Write-Host "  2. В Cline проверьте, что MCP-сервер '$ServerKey' активен."
Write-Host '  3. Если GitLab недоступен — убедитесь, что подключён корпоративный VPN.'
Write-Host ''
Write-Host 'При проблемах обращайтесь к администратору gitlab-mcp.'
Read-Host 'Нажмите Enter для выхода'
