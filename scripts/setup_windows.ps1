param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

$PythonCommand = Get-Command py -ErrorAction SilentlyContinue
if ($null -eq $PythonCommand) {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
}
if ($null -eq $PythonCommand) {
    throw "Python 3.11 was not found. Install Python from python.org, then run this script again."
}

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    if ($PythonCommand.Name -eq "py.exe") {
        & $PythonCommand.Source -3.11 -m venv (Join-Path $ProjectRoot ".venv")
    } else {
        & $PythonCommand.Source -m venv (Join-Path $ProjectRoot ".venv")
    }
}

if (-not $SkipInstall) {
    & $VenvPython -m pip install -e ".[dev]"
}

Write-Host "Setup complete. No system-wide Python or Node files were changed."
Write-Host "1. Start Ollama and make sure an installed model exists, for example: ollama list"
Write-Host "2. Run: .\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000"
Write-Host "3. Open: http://127.0.0.1:8000"
Write-Host "4. Check: .\.venv\Scripts\python.exe scripts\check_dependencies.py"
