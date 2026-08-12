param(
    [int]$Port = 8743,
    [switch]$UsePublicOsrm,
    [ValidateRange(1, 50)]
    [int]$PublicCandidateCap = 24
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Snapshot = Join-Path $RepoRoot "data\raw\epdk\2026-08-09\response.json"
$Manifest = Join-Path $RepoRoot "data\processed\epdk\2026-08-09\manifest.json"

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Project Python was not found at $PythonExe"
}
if (-not (Test-Path -LiteralPath $Snapshot) -or -not (Test-Path -LiteralPath $Manifest)) {
    throw "The local EPDK snapshot and manifest are required for custom-point planning."
}

$OsrmEndpoint = "http://127.0.0.1:5000"
$DemoArguments = @(
    "-m", "chargepath.demo",
    "--mode", "integration",
    "--port", $Port,
    "--station-snapshot", $Snapshot,
    "--station-manifest", $Manifest
)

if ($UsePublicOsrm) {
    $OsrmEndpoint = "https://router.project-osrm.org"
    $DemoArguments += @(
        "--allow-remote-osrm",
        "--candidate-cap", $PublicCandidateCap
    )
    Write-Warning "Selected origin/destination coordinates will be sent to the public OSRM demo endpoint."
}

$DemoArguments += @("--osrm-endpoint", $OsrmEndpoint)

Write-Host "ChargePath custom map: http://127.0.0.1:$Port"
Write-Host "OSRM endpoint: $OsrmEndpoint"
& $PythonExe @DemoArguments
exit $LASTEXITCODE
