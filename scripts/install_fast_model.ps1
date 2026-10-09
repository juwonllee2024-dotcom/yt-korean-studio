param(
    [switch]$SkipInstall,
    [string]$RepoId = "Helsinki-NLP/opus-mt-tc-big-en-ko",
    [string]$ModelDir = "",
    [string]$TokenizerDir = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Project virtual environment not found. Run .\scripts\setup_windows.ps1 first."
}

if (-not $SkipInstall) {
    & $VenvPython -m pip install -e ".[fast]"
}

$Arguments = @("scripts\install_fast_model.py", "--repo-id", $RepoId)
if ($ModelDir) { $Arguments += @("--model-dir", $ModelDir) }
if ($TokenizerDir) { $Arguments += @("--tokenizer-dir", $TokenizerDir) }
& $VenvPython @Arguments
if ($LASTEXITCODE -ne 0) {
    throw "Fast model setup failed with exit code $LASTEXITCODE."
}

Write-Host "Hybrid translation is ready. Restart the local server if it was already running."
