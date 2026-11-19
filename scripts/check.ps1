$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$python = @(
    (Join-Path $repositoryRoot ".venv\Scripts\python.exe"),
    (Join-Path $repositoryRoot ".venv/bin/python")
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $python) { $python = "python" }

$steps = @(
    @("-m", "pytest", "-q"),
    @("scripts/plan_synthetic_trip.py"),
    @("-m", "ruff", "check", "."),
    @("-m", "ruff", "format", "--check", "."),
    @("-m", "mypy")
)

Push-Location $repositoryRoot
try {
    foreach ($arguments in $steps) {
        & $python @arguments
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    Write-Host "All project checks passed."
} finally {
    Pop-Location
}
