$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Test-WebReady([string]$url) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 2 -ErrorAction Stop
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500)
    } catch { return $false }
}

Write-Host ''
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host '              TEAMSYNC PUBLIC HTTPS STARTER' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host ''

# Backend: use the existing server if it is already running.
Write-Host 'Checking TeamSync backend on port 8000...' -ForegroundColor Yellow
if (Test-WebReady 'http://127.0.0.1:8000/api/health') {
    Write-Host 'Backend is already running on port 8000.' -ForegroundColor Green
} else {
    Write-Host 'Starting TeamSync backend...' -ForegroundColor Cyan
    Start-Process cmd.exe -ArgumentList '/k','call run_backend.bat' -WorkingDirectory $root | Out-Null

    $backendReady = $false
    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep -Seconds 1
        if (Test-WebReady 'http://127.0.0.1:8000/api/health') {
            $backendReady = $true
            break
        }
    }
    if (-not $backendReady) {
        Write-Host 'Backend did not become ready on port 8000.' -ForegroundColor Red
        Write-Host 'Check the backend command window for the error.' -ForegroundColor Yellow
        Read-Host 'Press Enter to exit'
        exit 1
    }
    Write-Host 'Backend is ready.' -ForegroundColor Green
}

# Frontend: reuse any running Vite server on 5173-5180, otherwise start one.
Write-Host ''
Write-Host 'Checking TeamSync frontend...' -ForegroundColor Yellow
$frontendPort = $null
for ($p = 5173; $p -le 5180; $p++) {
    if (Test-WebReady ("http://127.0.0.1:{0}" -f $p)) {
        $frontendPort = $p
        break
    }
}
if ($frontendPort) {
    Write-Host ("Frontend is already running on port {0}." -f $frontendPort) -ForegroundColor Green
} else {
    Write-Host 'Starting TeamSync frontend...' -ForegroundColor Cyan
    Start-Process cmd.exe -ArgumentList '/k','call run_frontend.bat' -WorkingDirectory $root | Out-Null
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 1
        for ($p = 5173; $p -le 5180; $p++) {
            if (Test-WebReady ("http://127.0.0.1:{0}" -f $p)) {
                $frontendPort = $p
                break
            }
        }
        if ($frontendPort) { break }
    }
    if (-not $frontendPort) {
        Write-Host 'Frontend did not become ready on ports 5173-5180.' -ForegroundColor Red
        Write-Host 'Check the frontend command window for the error.' -ForegroundColor Yellow
        Read-Host 'Press Enter to exit'
        exit 1
    }
    Write-Host ("Frontend is ready on port {0}." -f $frontendPort) -ForegroundColor Green
}

# Find or download cloudflared automatically.
Write-Host ''
Write-Host 'Preparing public HTTPS link...' -ForegroundColor Yellow
$tools = Join-Path $root 'tools'
New-Item -ItemType Directory -Force -Path $tools | Out-Null
$cloudflared = Join-Path $tools 'cloudflared.exe'

if (-not (Test-Path $cloudflared)) {
    try { $cmd = Get-Command cloudflared -ErrorAction Stop; $cloudflared = $cmd.Source } catch {}
}

if (-not (Test-Path $cloudflared) -and $cloudflared -notmatch 'cloudflared.exe$') {
    $cloudflared = Join-Path $tools 'cloudflared.exe'
}

if (-not (Test-Path $cloudflared)) {
    Write-Host 'Downloading Cloudflare tunnel helper (one time only)...' -ForegroundColor Cyan
    try {
        Invoke-WebRequest -UseBasicParsing `
            -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' `
            -OutFile $cloudflared `
            -TimeoutSec 60
    } catch {
        Write-Host 'Could not download Cloudflared.' -ForegroundColor Red
        Write-Host $_.Exception.Message -ForegroundColor Red
        Write-Host 'TeamSync is still available locally at http://localhost:5173' -ForegroundColor Yellow
        Read-Host 'Press Enter to exit'
        exit 1
    }
}

if (-not (Test-Path $cloudflared)) {
    Write-Host 'Cloudflared was not available.' -ForegroundColor Red
    Read-Host 'Press Enter to exit'
    exit 1
}

Write-Host 'Starting public HTTPS tunnel...' -ForegroundColor Cyan
$outLog = Join-Path $root 'cloudflared.log'
$errLog = Join-Path $root 'cloudflared-error.log'
Remove-Item $outLog,$errLog -Force -ErrorAction SilentlyContinue

$proc = Start-Process -FilePath $cloudflared `
    -ArgumentList @('tunnel','--url',('http://127.0.0.1:{0}' -f $frontendPort),'--protocol','http2','--edge-ip-version','4','--no-autoupdate','--loglevel','info') `
    -WorkingDirectory $root `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -PassThru

$public = $null
Write-Host 'Waiting for Cloudflare to create and stabilize the HTTPS link (up to 120 seconds)...' -ForegroundColor Yellow

# Cloudflare can print a Quick Tunnel hostname a few seconds before its DNS record
# is usable. Keep the process alive and verify DNS + HTTPS before opening Edge.
for ($i = 0; $i -lt 120; $i++) {
    Start-Sleep -Seconds 1
    $txt = ''
    if (Test-Path $outLog) { $txt += (Get-Content $outLog -Raw -ErrorAction SilentlyContinue) }
    if (Test-Path $errLog) { $txt += "`n" + (Get-Content $errLog -Raw -ErrorAction SilentlyContinue) }
    $m = [regex]::Match($txt, 'https://[a-zA-Z0-9-]+\.trycloudflare\.com')
    if ($m.Success) {
        $candidate = $m.Value.TrimEnd('/')
        if (-not $proc.HasExited) {
            # First wait for the public hostname to resolve. This avoids opening
            # Edge on a just-created hostname that still returns NXDOMAIN.
            try {
                $hostName = ([uri]$candidate).Host
                Resolve-DnsName $hostName -Type A -ErrorAction Stop | Out-Null
                if (Test-WebReady $candidate) {
                    $public = $candidate
                    break
                }
            } catch { }
        }
    }
    if ($proc.HasExited) { break }
}

if (-not $public) {
    Write-Host ''
    Write-Host 'Cloudflare did not produce a reachable public HTTPS link.' -ForegroundColor Red
    Write-Host 'The local TeamSync application is working at http://localhost:5173' -ForegroundColor Yellow
    if (Test-Path $outLog) { Write-Host ''; Write-Host 'Cloudflare output:' -ForegroundColor Cyan; Get-Content $outLog -ErrorAction SilentlyContinue | Select-Object -Last 20 }
    if (Test-Path $errLog) { Write-Host ''; Write-Host 'Cloudflare errors:' -ForegroundColor Cyan; Get-Content $errLog -ErrorAction SilentlyContinue | Select-Object -Last 20 }
    Read-Host 'Press Enter to exit'
    exit 1
}

$publicFile = Join-Path $root 'backend\public_url.txt'
$public | Set-Content -Encoding UTF8 $publicFile

Write-Host ''
Write-Host '============================================================' -ForegroundColor Green
Write-Host '              TEAMSYNC PUBLIC HTTPS LINK' -ForegroundColor Green
Write-Host '============================================================' -ForegroundColor Green
Write-Host $public -ForegroundColor White
Write-Host '============================================================' -ForegroundColor Green
Write-Host ''
Write-Host 'Copy this link and share it in WhatsApp.' -ForegroundColor Yellow
Write-Host 'Cloudflare tunnel is reachable. Opening Edge now.' -ForegroundColor Green
Write-Host 'Keep this window open while using the public link.' -ForegroundColor Yellow
Start-Process $public
while (-not $proc.HasExited) {
    Start-Sleep -Seconds 2
}
Write-Host ''
Write-Host 'The public tunnel has stopped. The temporary URL is no longer valid.' -ForegroundColor Red
Write-Host 'Run START.bat again to create a new verified HTTPS link.' -ForegroundColor Yellow
Read-Host 'Press Enter to close'
