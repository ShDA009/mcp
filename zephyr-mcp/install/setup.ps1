<#
    Установочный скрипт zephyr-mcp (Zephyr Scale / ATM MCP-сервер) для Windows.
    Запуск: правый клик -> "Выполнить с помощью PowerShell",
            либо:  powershell -ExecutionPolicy Bypass -File setup.ps1
    Прав администратора не требует.
#>

$ErrorActionPreference = 'Stop'

# --- Константы --------------------------------------------------------------
$GitUrl     = 'git+https://github.com/ShDA009/mcp.git#subdirectory=zephyr-mcp'
$McpEntry   = 'zephyr-mcp'
$ServerKey  = 'zephyr-scale'

function Write-Info($m) { Write-Host $m -ForegroundColor Cyan }
function Write-Ok  ($m) { Write-Host $m -ForegroundColor Green }
function Write-Warn2($m){ Write-Host $m -ForegroundColor Yellow }
function Die($m) { Write-Host $m -ForegroundColor Red; Read-Host 'Нажмите Enter для выхода'; exit 1 }

Write-Info '== Установка zephyr-mcp для Windows =='

# --- 1. Поиск uv/uvx --------------------------------------------------------
function Find-Uvx {
    # 1) типичное место установки
    $cand = Join-Path $env:USERPROFILE '.local\bin\uvx.exe'
    if (Test-Path $cand) { return $cand }
    # 2) в PATH
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

$ConfDir = Join-Path $env:USERPROFILE '.zephyr-mcp'
$EnvFile = Join-Path $ConfDir '.env'
if (-not (Test-Path $ConfDir)) { New-Item -ItemType Directory -Path $ConfDir -Force | Out-Null }

# --- 3. Прочитать существующий .env -----------------------------------------
function Get-EnvValue($key) {
    if (-not (Test-Path $EnvFile)) { return $null }
    $line = Select-String -Path $EnvFile -Pattern "^$([regex]::Escape($key))=" -ErrorAction SilentlyContinue |
            Select-Object -Last 1
    if ($line) { return ($line.Line -replace "^$([regex]::Escape($key))=", '') }
    return $null
}

$curUrl   = Get-EnvValue 'ZEPHYR_BASE_URL'
$curToken = Get-EnvValue 'ZEPHYR_API_TOKEN'
$haveToken = [bool]$curToken

$change = $true
if ((Test-Path $EnvFile) -and $curUrl) {
    Write-Info "Найден существующий конфиг: $EnvFile"
    Write-Host ("  ZEPHYR_BASE_URL  = {0}" -f ($(if($curUrl){$curUrl}else{'<не задан>'})))
    Write-Host ("  ZEPHYR_API_TOKEN = {0}" -f ($(if($haveToken){'******** (сохранён)'}else{'<не задан>'})))
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
    Write-Info 'Введите параметры подключения к Zephyr Scale / ATM (Jira):'
    $ZephyrUrl = Read-NonEmpty '  Base URL (https://jira.example.com)' $curUrl
    while ($true) {
        $sec = Read-Host '  Personal Access Token (ввод скрыт)' -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
        try   { $ZephyrToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        if (-not [string]::IsNullOrWhiteSpace($ZephyrToken)) { break }
        Write-Warn2 'Токен не может быть пустым.'
    }
} else {
    $ZephyrUrl = $curUrl; $ZephyrToken = $curToken
    Write-Ok 'Креды оставлены без изменений.'
}

# --- 4. Записать .env -------------------------------------------------------
$envLines = @(
    "ZEPHYR_BASE_URL=$ZephyrUrl"
    "ZEPHYR_API_TOKEN=$ZephyrToken"
)
# без BOM, LF
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($EnvFile, ($envLines -join "`n") + "`n", $utf8)

# ограничить доступ к файлу текущим пользователем
try {
    icacls $EnvFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
} catch { Write-Warn2 "Не удалось ужесточить права на $EnvFile (продолжаю)." }
Write-Ok "Креды сохранены в $EnvFile"

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

# Креды НЕ попадают в этот JSON: zephyr_mcp/config.py сам читает их из
# %USERPROFILE%\.zephyr-mcp\.env при старте.
$serverObj = [pscustomobject]@{
    command       = $UvxBin
    args          = @('--from', $GitUrl, $McpEntry)
    disabled      = $false
    transportType = 'stdio'
}

# Обновляем секцию на месте (Add-Member -Force перезаписывает существующую, не дублируя).
$cfg.mcpServers | Add-Member -NotePropertyName $ServerKey -NotePropertyValue $serverObj -Force

$json = $cfg | ConvertTo-Json -Depth 10
[System.IO.File]::WriteAllText($ClineCfg, $json + "`n", $utf8)
Write-Ok "  секция '$ServerKey' обновлена (командой $UvxBin)"

# --- 6. Проверочный вызов ---------------------------------------------------
Write-Info 'Проверяю, что пакет ставится и запускается (uvx ... --help)...'
$ok = $false
try {
    & $UvxBin --from $GitUrl $McpEntry --help *> $null
    if ($LASTEXITCODE -eq 0) { $ok = $true }
} catch { $ok = $false }
if ($ok) {
    Write-Ok 'Проверочный запуск успешен.'
} else {
    Write-Warn2 'Проверочный запуск завершился с ненулевым кодом.'
    Write-Warn2 'Это не всегда ошибка (сервер может не поддерживать --help). Если Cline не подключится:'
    Write-Warn2 '  - проверьте доступ к github.com (интернет / прокси);'
    Write-Warn2 '  - проверьте, что подключён корпоративный VPN (для доступа к Jira/ATM).'
}

# --- 7. Итог ----------------------------------------------------------------
Write-Host ''
Write-Ok '== Готово =='
Write-Host "  uv/uvx:  $UvxBin"
Write-Host "  Конфиг:  $EnvFile"
Write-Host "  Cline:   $ClineCfg (сервер '$ServerKey')"
Write-Host ''
Write-Info 'Дальше:'
Write-Host '  1. Полностью перезапустите VS Code (и Cline).'
Write-Host "  2. В Cline проверьте, что MCP-сервер '$ServerKey' активен."
Write-Host '  3. Если Jira/ATM недоступен — убедитесь, что подключён корпоративный VPN.'
Write-Host ''
Write-Host 'При проблемах обращайтесь к администратору zephyr-mcp.'
Read-Host 'Нажмите Enter для выхода'
