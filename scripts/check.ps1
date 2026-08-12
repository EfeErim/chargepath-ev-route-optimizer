$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$windowsVenvPython = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
$posixVenvPython = Join-Path $repositoryRoot ".venv/bin/python"
$pythonArguments = @()

if ($env:CHARGEPATH_PYTHON) {
    if (-not (Test-Path -LiteralPath $env:CHARGEPATH_PYTHON -PathType Leaf)) {
        throw "CHARGEPATH_PYTHON does not identify a Python executable."
    }
    $pythonExecutable = $env:CHARGEPATH_PYTHON
} elseif (Test-Path $windowsVenvPython) {
    $pythonExecutable = $windowsVenvPython
} elseif (Test-Path $posixVenvPython) {
    $pythonExecutable = $posixVenvPython
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonExecutable = "py"
    $pythonArguments = @("-3.11")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonExecutable = "python"
} else {
    throw "Python 3.11+ was not found. Create .venv or install Python."
}

Push-Location $repositoryRoot
try {
& $pythonExecutable @pythonArguments -m pytest -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $pythonExecutable @pythonArguments -m compileall -q src tests examples
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $pythonExecutable @pythonArguments examples/run_synthetic_demo.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $pythonExecutable @pythonArguments -m ruff check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $pythonExecutable @pythonArguments -m ruff format --check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $pythonExecutable @pythonArguments -m mypy
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $pythonExecutable @pythonArguments -m pip check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $pythonExecutable @pythonArguments scripts/check_wheel.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $pythonExecutable @pythonArguments scripts/audit_release.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "All project checks passed."
} finally {
    Pop-Location
}
