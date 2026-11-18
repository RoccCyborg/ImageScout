param(
    [switch]$LanAccess
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== ImageScout 2.2 local backend ===" -ForegroundColor Cyan

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python launcher 'py' was not found. Install Python 3.11+ and try again."
}

if (-not (Test-Path ".venv")) {
    $created = $false
    foreach ($version in @("3.13", "3.12", "3.11")) {
        & py "-$version" -m venv .venv 2>$null
        if ($LASTEXITCODE -eq 0) {
            $created = $true
            Write-Host "Created virtual environment with Python $version."
            break
        }
        if (Test-Path ".venv") { Remove-Item ".venv" -Recurse -Force }
    }
    if (-not $created) {
        throw "Python 3.11, 3.12 or 3.13 was not found. Install Python 3.11+ and retry."
    }
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Virtual environment Python was not created."
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env. Add BRAVE_SEARCH_API_KEY there if you have one." -ForegroundColor Yellow
}

New-Item -ItemType Directory -Force -Path "data" | Out-Null

& $python -m pip install --upgrade pip
& $python -m pip install -r requirements.txt

if ($LanAccess) {
    $bindHost = "0.0.0.0"
    Write-Host "LAN mode enabled. Only use on a trusted private Wi-Fi network." -ForegroundColor Yellow
    Write-Host "Do not configure router port forwarding or expose port 8000 publicly." -ForegroundColor Yellow
} else {
    $bindHost = "127.0.0.1"
}

Write-Host "Dashboard: http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "API docs:  http://127.0.0.1:8000/docs" -ForegroundColor Green
if ($LanAccess) {
    $ips = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" }
    foreach ($ip in $ips) {
        Write-Host "LAN dashboard: http://$($ip.IPAddress):8000" -ForegroundColor Green
    }
}
Write-Host "Stop the server with Ctrl+C. Local SQLite index: data\image_index.db" -ForegroundColor Gray

& $python -m uvicorn api:app --host $bindHost --port 8000
