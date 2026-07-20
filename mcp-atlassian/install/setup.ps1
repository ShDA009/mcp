<#
    Установочный скрипт mcp-atlassian (Jira + Confluence через сторонний
    сервер sooperset/mcp-atlassian) для Windows.
    Запуск: правый клик -> "Выполнить с помощью PowerShell",
            либо:  powershell -ExecutionPolicy Bypass -File setup.ps1
    Прав администратора не требует.
#>

$ErrorActionPreference = 'Stop'

# --- Константы --------------------------------------------------------------
$ServerKey = 'mcp-atlassian'
$VersionsRawUrl = 'https://raw.githubusercontent.com/ShDA009/mcp/master/mcp-versions.txt'
# Fallback-версия, если mcp-versions.txt никогда не удастся скачать (первый запуск
# без сети). Лаунчер подтягивает актуальную версию из репо при каждом старте.
$FallbackAtlassianSpec = 'mcp-atlassian>=0.23,<0.24'

function Write-Info($m) { Write-Host $m -ForegroundColor Cyan }
function Write-Ok  ($m) { Write-Host $m -ForegroundColor Green }
function Write-Warn2($m){ Write-Host $m -ForegroundColor Yellow }
function Die($m) { Write-Host $m -ForegroundColor Red; Read-Host 'Нажмите Enter для выхода'; exit 1 }

Write-Info '== Установка mcp-atlassian для Windows =='

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
$ClineDir = Join-Path $env:APPDATA 'Code\User\globalStorage\saoudrizwan.claude-dev\settings'
$ClineCfg = Join-Path $ClineDir 'cline_mcp_settings.json'

$ConfDir = Join-Path $env:USERPROFILE '.mcp-atlassian'
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

$curConfluenceUrl = Get-EnvValue 'CONFLUENCE_URL'
$curJiraUrl       = Get-EnvValue 'JIRA_URL'
$haveConfluenceToken = [bool](Get-EnvValue 'CONFLUENCE_PERSONAL_TOKEN')
$haveJiraToken       = [bool](Get-EnvValue 'JIRA_PERSONAL_TOKEN')

$change = $true
if ((Test-Path $EnvFile) -and $curJiraUrl) {
    Write-Info "Найден существующий конфиг: $EnvFile"
    Write-Host ("  CONFLUENCE_URL             = {0}" -f ($(if($curConfluenceUrl){$curConfluenceUrl}else{'<не задан>'})))
    Write-Host ("  CONFLUENCE_PERSONAL_TOKEN  = {0}" -f ($(if($haveConfluenceToken){'******** (сохранён)'}else{'<не задан>'})))
    Write-Host ("  JIRA_URL                   = {0}" -f ($(if($curJiraUrl){$curJiraUrl}else{'<не задан>'})))
    Write-Host ("  JIRA_PERSONAL_TOKEN        = {0}" -f ($(if($haveJiraToken){'******** (сохранён)'}else{'<не задан>'})))
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
    Write-Info 'Введите параметры подключения к Atlassian (Jira Server/DC + Confluence):'
    $ConfluenceUrl   = Read-NonEmpty '  Confluence URL (https://wiki.example.com)' $curConfluenceUrl
    $ConfluenceToken = Read-SecretNonEmpty '  Confluence Personal Access Token (ввод скрыт)'
    $JiraUrl         = Read-NonEmpty '  Jira URL (https://jira.example.com)' $curJiraUrl
    $JiraToken       = Read-SecretNonEmpty '  Jira Personal Access Token (ввод скрыт)'
} else {
    $ConfluenceUrl   = $curConfluenceUrl
    $ConfluenceToken = Get-EnvValue 'CONFLUENCE_PERSONAL_TOKEN'
    $JiraUrl         = $curJiraUrl
    $JiraToken       = Get-EnvValue 'JIRA_PERSONAL_TOKEN'
    Write-Ok 'Креды оставлены без изменений.'
}

# --- 4. Записать .env -------------------------------------------------------
$envLines = @(
    "CONFLUENCE_URL=$ConfluenceUrl"
    "CONFLUENCE_PERSONAL_TOKEN=$ConfluenceToken"
    "JIRA_URL=$JiraUrl"
    "JIRA_PERSONAL_TOKEN=$JiraToken"
    'VERIFY_SSL=false'
    'CONFLUENCE_SSL_VERIFY=false'
    'JIRA_SSL_VERIFY=false'
    'PYTHONHTTPSVERIFY=0'
    'PYTHONUNBUFFERED=1'
)
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($EnvFile, ($envLines -join "`n") + "`n", $utf8)

try {
    icacls $EnvFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
} catch { Write-Warn2 "Не удалось ужесточить права на $EnvFile (продолжаю)." }
Write-Ok "Креды сохранены в $EnvFile"

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
    "`$ConfDir = '$ConfDir'"
    "`$Cache = Join-Path `$ConfDir 'mcp-versions.txt'"
    "`$RawUrl = '$VersionsRawUrl'"
    "`$AtlassianSpec = '$FallbackAtlassianSpec'"
    ''
    '# 1) Попробовать обновить кеш версии из репо (короткий таймаут — не вешать старт).'
    'try {'
    '    Invoke-WebRequest -Uri $RawUrl -OutFile "$Cache.new" -TimeoutSec 3 -UseBasicParsing | Out-Null'
    '    Move-Item -Force "$Cache.new" $Cache'
    '} catch {'
    '    Remove-Item -Force "$Cache.new" -ErrorAction SilentlyContinue'
    '}'
    ''
    '# 2) Взять ATLASSIAN_SPEC из кеша, только если строка похожа на безопасный'
    '#    пакетный спецификатор (буквы/цифры/@/./_/-/,/=/</>/^/~, слэш для скоупов).'
    'if (Test-Path $Cache) {'
    '    $line = Get-Content $Cache | Where-Object { $_ -match ''^ATLASSIAN_SPEC='' } | Select-Object -Last 1'
    '    if ($line) {'
    '        $value = $line -replace ''^ATLASSIAN_SPEC='', '''''
    '        $value = $value.Trim(''"'')'
    '        if ($value -match ''^[A-Za-z0-9@/._,=<>^~-]+$'') { $AtlassianSpec = $value }'
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
    "& '$UvxBin' --from `$AtlassianSpec mcp-atlassian --env-file `$envFile @args"
    'exit $LASTEXITCODE'
)
[System.IO.File]::WriteAllText($LaunchScript, ($launchScriptLines -join "`r`n") + "`r`n", $utf8)

$launchCmdLines = @(
    '@echo off'
    "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""$LaunchScript"" %*"
)
[System.IO.File]::WriteAllText($LaunchFile, ($launchCmdLines -join "`r`n") + "`r`n", $utf8)
Write-Ok "Лаунчер сгенерирован: $LaunchFile"

# --- 6. Обновить конфиг Cline идемпотентно ----------------------------------
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

# --- 7. Проверочный вызов ---------------------------------------------------
Write-Info 'Проверяю, что пакет ставится и запускается (launch.cmd --help)...'
$ok = $false
try {
    & $LaunchFile --help *> $null
    if ($LASTEXITCODE -eq 0) { $ok = $true }
} catch { $ok = $false }
if ($ok) {
    Write-Ok 'Проверочный запуск успешен.'
} else {
    Write-Warn2 'Проверочный запуск завершился с ненулевым кодом.'
    Write-Warn2 'Если Cline не подключится:'
    Write-Warn2 '  - проверьте доступ к github.com и pypi.org (интернет / прокси);'
    Write-Warn2 '  - проверьте, что подключён корпоративный VPN (для доступа к Jira/Confluence).'
}

# --- 8. Итог ----------------------------------------------------------------
Write-Host ''
Write-Ok '== Готово =='
Write-Host "  uv/uvx:   $UvxBin"
Write-Host "  Конфиг:   $EnvFile"
Write-Host "  Лаунчер:  $LaunchFile"
Write-Host "  Cline:    $ClineCfg (сервер '$ServerKey')"
Write-Host ''
Write-Info 'Дальше:'
Write-Host '  1. Полностью перезапустите VS Code (и Cline).'
Write-Host "  2. В Cline проверьте, что MCP-сервер '$ServerKey' активен."
Write-Host '  3. Если Jira/Confluence недоступны — убедитесь, что подключён корпоративный VPN.'
Write-Host ''
Write-Host 'При проблемах обращайтесь к администратору mcp-atlassian.'
Read-Host 'Нажмите Enter для выхода'
