$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Test-WebReady([string]$url) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 5 -Headers @{'User-Agent'='Mozilla/5.0'} -ErrorAction Stop
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500)
    } catch { return $false }
}

Write-Host ''
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host '        TEAMSYNC - AUTOMATIC PUBLIC MEETING LINK' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host ''

# Backend
if (-not (Test-WebReady 'http://127.0.0.1:8000/api/health')) {
    Write-Host 'Starting backend...' -ForegroundColor Cyan
    Start-Process cmd.exe -ArgumentList '/k','call run_backend.bat' -WorkingDirectory $root | Out-Null
    $ok = $false
    for ($i=0; $i -lt 45; $i++) {
        Start-Sleep 1
        if (Test-WebReady 'http://127.0.0.1:8000/api/health') { $ok=$true; break }
    }
    if (-not $ok) { Write-Host 'Backend did not start. Check its command window.' -ForegroundColor Red; Read-Host 'Press Enter to exit'; exit 1 }
}
Write-Host 'Backend ready.' -ForegroundColor Green

# Frontend: detect 5173-5180.
$frontendPort = $null
for ($p=5173; $p -le 5180; $p++) {
    if (Test-WebReady ("http://127.0.0.1:{0}" -f $p)) { $frontendPort=$p; break }
}
if (-not $frontendPort) {
    Write-Host 'Starting frontend...' -ForegroundColor Cyan
    Start-Process cmd.exe -ArgumentList '/k','call run_frontend.bat' -WorkingDirectory $root | Out-Null
    for ($i=0; $i -lt 90; $i++) {
        Start-Sleep 1
        for ($p=5173; $p -le 5180; $p++) {
            if (Test-WebReady ("http://127.0.0.1:{0}" -f $p)) { $frontendPort=$p; break }
        }
        if ($frontendPort) { break }
    }
}
if (-not $frontendPort) { Write-Host 'Frontend did not start on ports 5173-5180.' -ForegroundColor Red; Read-Host 'Press Enter to exit'; exit 1 }
Write-Host ("Frontend ready on port {0}." -f $frontendPort) -ForegroundColor Green

# Remove stale public URL so the UI does not accidentally use an old temporary URL.
$publicFile = Join-Path $root 'backend\public_url.txt'
Remove-Item $publicFile -Force -ErrorAction SilentlyContinue

Write-Host ''
Write-Host 'Starting LocalTunnel automatically...' -ForegroundColor Yellow
Write-Host 'If this is the first run, npm may download LocalTunnel for a moment.' -ForegroundColor DarkGray

$log = Join-Path $root 'localtunnel.log'
Remove-Item $log -Force -ErrorAction SilentlyContinue

# npx writes the LocalTunnel URL to its console output. Redirect it to a file and
# poll until a loca.lt URL appears, then save it for the TeamSync frontend/backend.
$proc = Start-Process cmd.exe -ArgumentList '/c',('npx --yes localtunnel --port {0} > "{1}" 2>&1' -f $frontendPort,$log) -WorkingDirectory $root -PassThru

$public = $null
for ($i=0; $i -lt 120; $i++) {
    Start-Sleep 1
    if (Test-Path $log) {
        $txt = Get-Content $log -Raw -ErrorAction SilentlyContinue
        if ([string]::IsNullOrWhiteSpace($txt)) { continue }
        $m = [regex]::Match([string]$txt,'https://[a-zA-Z0-9-]+\.loca\.lt')
        if ($m.Success) {
            $candidate = $m.Value.TrimEnd('/')
            # Verify that the temporary URL is actually reachable before publishing it.
            if (Test-WebReady $candidate) {
                $public = $candidate
                break
            }
        }
    }
    if ($proc.HasExited -and -not $public) { break }
}

if (-not $public) {
    Write-Host ''
    Write-Host 'LocalTunnel could not create a reachable public link.' -ForegroundColor Red
    Write-Host 'Your local app is still available at:' -ForegroundColor Yellow
    Write-Host ("http://localhost:{0}" -f $frontendPort) -ForegroundColor White
    if (Test-Path $log) { Write-Host ''; Get-Content $log -ErrorAction SilentlyContinue | Select-Object -Last 25 }
    Read-Host 'Press Enter to exit'
    exit 1
}

$public | Set-Content -Encoding UTF8 $publicFile

Write-Host ''
Write-Host '============================================================' -ForegroundColor Green
Write-Host '           TEAMSYNC PUBLIC MEETING LINK READY' -ForegroundColor Green
Write-Host '============================================================' -ForegroundColor Green
Write-Host $public -ForegroundColor White
Write-Host '============================================================' -ForegroundColor Green
Write-Host ''
Write-Host 'The TeamSync Share Meeting window will update automatically.' -ForegroundColor Green
Write-Host 'Copy Link / WhatsApp / Telegram will now use the HTTPS URL.' -ForegroundColor Yellow
Write-Host 'Keep this window open while using the public meeting link.' -ForegroundColor Yellow
Start-Process $public
while (-not $proc.HasExited) { Start-Sleep 2 }
Write-Host ''
Write-Host 'LocalTunnel stopped. The temporary public URL is no longer valid.' -ForegroundColor Red
Read-Host 'Press Enter to close'
