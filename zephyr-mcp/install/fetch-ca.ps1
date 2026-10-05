<#
    Собирает цепочку CA для сервера Jira/Zephyr в один PEM-файл для ZEPHYR_CA_BUNDLE.
    Windows-аналог fetch-ca.sh. openssl не нужен: цепочку строит сама Windows
    (недостающие CA докачиваются по AIA из сертификата, берутся из системного
    хранилища). Закрытых ключей не касается, прав администратора не требует.

    Запуск:
        powershell -ExecutionPolicy Bypass -File fetch-ca.ps1 -Url https://tasks.example.com
        powershell -ExecutionPolicy Bypass -File fetch-ca.ps1 -Url https://tasks.example.com -Out C:\path\ca.pem
#>
param(
    [Parameter(Mandatory = $true)][string]$Url,
    [string]$Out = (Join-Path $env:USERPROFILE '.zephyr-mcp\ca.pem')
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if ($Url -notmatch '^https?://') { $Url = "https://$Url" }
$uri = [Uri]$Url
$port = if ($uri.Port -gt 0) { $uri.Port } else { 443 }

# Получаем leaf-сертификат; проверку намеренно пропускаем — цепочку строим сами.
$tcp = New-Object System.Net.Sockets.TcpClient($uri.Host, $port)
try {
    $ssl = New-Object System.Net.Security.SslStream($tcp.GetStream(), $false, { $true })
    $tls = [System.Security.Authentication.SslProtocols]::Tls12
    try { $tls = $tls -bor [System.Security.Authentication.SslProtocols]::Tls13 } catch { }
    $ssl.AuthenticateAsClient($uri.Host, $null, $tls, $false)
    $leaf = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($ssl.RemoteCertificate)
} finally {
    $tcp.Close()
}

$chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
$chain.ChainPolicy.RevocationMode = [System.Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
[void]$chain.Build($leaf)

$cas = @($chain.ChainElements | Select-Object -Skip 1 | ForEach-Object { $_.Certificate })
if ($cas.Count -eq 0) {
    throw "Не удалось получить ни одного CA для $($uri.Host). Запросите корневой CA у администраторов."
}

$pem = New-Object System.Text.StringBuilder
foreach ($c in $cas) {
    $b64 = [Convert]::ToBase64String($c.RawData, [Base64FormattingOptions]::InsertLineBreaks)
    [void]$pem.Append("-----BEGIN CERTIFICATE-----`n$b64`n-----END CERTIFICATE-----`n")
}

Write-Host 'Сертификаты в цепочке (сверьте с тем, чему доверяете):'
foreach ($c in $cas) { Write-Host "  - $($c.Subject)" }

$root = $cas[-1]
if ($root.Subject -ne $root.Issuer) {
    Write-Warning "Последний сертификат не самоподписанный: корневой CA в цепочке не найден, запросите его у администраторов."
}

# Сначала пишем во временный файл и проверяем; $Out трогаем только при успехе.
$tmpFile = [System.IO.Path]::GetTempFileName()
try {
    # UTF-8 без BOM: BOM ломает разбор PEM.
    [System.IO.File]::WriteAllText($tmpFile, $pem.ToString(), (New-Object System.Text.UTF8Encoding($false)))

    # Проверяем именно файл, а не системное хранилище: curl.exe есть в Windows 10+.
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & $curl.Source -sS -o NUL --cacert $tmpFile "https://$($uri.Authority)/"
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Проверка TLS через curl не прошла (в цепочке может не хватать корневого CA); $Out не изменён."
            exit 1
        }
        Write-Host 'Проверка TLS: OK'
    } else {
        Write-Host 'curl.exe не найден — проверку TLS пропускаю.'
    }

    $dir = Split-Path -Parent ([System.IO.Path]::GetFullPath($Out))
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    Copy-Item -Force $tmpFile $Out
} finally {
    Remove-Item -Force $tmpFile -ErrorAction SilentlyContinue
}

Write-Host "Сохранено: $Out"
# Прописываем путь в env-файл сервера (остальные строки не трогаем).
$envFile = Join-Path $env:USERPROFILE '.zephyr-mcp\.env'
if (Test-Path $envFile) {
    $fullOut = [System.IO.Path]::GetFullPath($Out)
    $lines = @(Get-Content -LiteralPath $envFile -Encoding UTF8)
    $done = $false
    $res = foreach ($l in $lines) {
        if ($l -match '^\s*ZEPHYR_CA_BUNDLE\s*=') {
            if (-not $done) { "ZEPHYR_CA_BUNDLE=$fullOut"; $done = $true }
        } else { $l }
    }
    $res = @($res)
    if (-not $done) { $res += "ZEPHYR_CA_BUNDLE=$fullOut" }
    [System.IO.File]::WriteAllText($envFile, (($res -join "`r`n") + "`r`n"), (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "ZEPHYR_CA_BUNDLE записан в $envFile"
} else {
    Write-Host "Файл $envFile не найден, добавьте вручную: ZEPHYR_CA_BUNDLE=$Out"
}
