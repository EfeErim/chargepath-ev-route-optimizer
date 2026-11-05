param(
    [int]$Port = 8743,
    [switch]$UsePublicOsrm,
    [ValidateRange(1, 50)]
    [int]$PublicCandidateCap = 24
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "launcher_common.ps1")

$Python = Resolve-ChargePathPython -RepoRoot $RepoRoot
if ($null -eq $Python) {
    throw (Get-PythonHelp)
}
$Snapshot = Resolve-EpdkSnapshot -RepoRoot $RepoRoot
if ($null -eq $Snapshot) {
    throw (Get-SnapshotHelp)
}

$OsrmEndpoint = "http://127.0.0.1:5000"
$DemoArguments = @(
    "-m", "chargepath.demo",
    "--mode", "integration",
    "--port", $Port,
    "--station-snapshot", $Snapshot.Snapshot,
    "--station-manifest", $Snapshot.Manifest
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
if ($Python.FromSource) {
    $env:PYTHONPATH = Join-Path $RepoRoot "src"
}

Write-Host "ChargePath custom map: http://127.0.0.1:$Port"
Write-Host "EPDK snapshot: $($Snapshot.Date)"
Write-Host "OSRM endpoint: $OsrmEndpoint"
& $Python.Executable @DemoArguments
exit $LASTEXITCODE
