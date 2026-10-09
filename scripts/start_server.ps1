$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Project virtual environment not found. Run scripts\setup_windows.ps1 first."
}

$Existing = Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($null -ne $Existing) {
    Write-Host "YT Korean Studio already running (PID $($Existing.OwningProcess))."
    Write-Host "Open http://127.0.0.1:8000"
    exit 0
}

$LogDirectory = Join-Path $ProjectRoot "data\server"
New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
$StdoutPath = Join-Path $LogDirectory "uvicorn.stdout.log"
$StderrPath = Join-Path $LogDirectory "uvicorn.stderr.log"

$Process = Start-Process -FilePath $PythonPath `
    -ArgumentList @("-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8000") `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $StdoutPath `
    -RedirectStandardError $StderrPath `
    -PassThru

$Ready = $false
for ($Attempt = 0; $Attempt -lt 15; $Attempt++) {
    Start-Sleep -Seconds 1
    try {
        $Health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2
        if ($Health.ok -eq $true) {
            $Ready = $true
            break
        }
    } catch {
        # Server still starting; inspect stderr only if all attempts fail.
    }
}

if (-not $Ready) {
    $ErrorText = Get-Content -LiteralPath $StderrPath -ErrorAction SilentlyContinue
    if ($ErrorText) { $ErrorText | Write-Error }
    if (-not $Process.HasExited) { Stop-Process -Id $Process.Id -ErrorAction SilentlyContinue }
    throw "YT Korean Studio did not start."
}

Write-Host "YT Korean Studio running in background (PID $($Process.Id))."
Write-Host "Open http://127.0.0.1:8000"
