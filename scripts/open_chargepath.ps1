param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8743,
    [switch]$UsePublicOsrm,
    [switch]$FixtureOnly,
    [switch]$NoBrowser,
    [ValidateRange(1, 50)]
    [int]$PublicCandidateCap = 24
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "launcher_common.ps1")
$AppUrl = "http://127.0.0.1:$Port/"
$HealthUrl = "${AppUrl}health"
$StartupSeconds = 30

function Test-ChargePathHealth {
    try {
        $Response = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 2
        if ($Response.StatusCode -ne 200) {
            return $false
        }
        $Payload = $Response.Content | ConvertFrom-Json
        return (
            $Payload.status -eq "ok" -and
            $Payload.service -eq "chargepath-demo" -and
            $Payload.api_version -eq 3
        )
    }
    catch {
        return $false
    }
}

function Confirm-PublicOsrm {
    Write-Host ""
    Write-Host "A local EPDK station snapshot is available for planning between any two map points."
    Write-Host "That mode sends the origin, destination, and candidate station coordinates you plan"
    Write-Host "with to the public OSRM demo server (router.project-osrm.org)."
    try {
        $Answer = Read-Host "Send coordinates to public OSRM? Answer n to open the synthetic demo [y/N]"
    }
    catch {
        return $false
    }
    return $Answer -match "^(y|yes|e|evet)$"
}

function Open-ChargePath {
    if ($NoBrowser) {
        Write-Host "ChargePath is running at $AppUrl"
    }
    else {
        Start-Process -FilePath $AppUrl
    }
}

if (Test-ChargePathHealth) {
    Open-ChargePath
    exit 0
}

$Python = Resolve-ChargePathPython -RepoRoot $RepoRoot
if ($null -eq $Python) {
    Write-Host (Get-PythonHelp)
    exit 1
}

$Snapshot = if ($FixtureOnly) { $null } else { Resolve-EpdkSnapshot -RepoRoot $RepoRoot }
if ($UsePublicOsrm -and $null -eq $Snapshot) {
    Write-Host (Get-SnapshotHelp)
    exit 1
}
$UseIntegration = $false
if ($null -ne $Snapshot) {
    $UseIntegration = $UsePublicOsrm -or (Confirm-PublicOsrm)
}
elseif (-not $FixtureOnly) {
    Write-Host (Get-SnapshotHelp)
    Write-Host "Opening the offline synthetic demo instead."
}

$DemoArguments = @("-m", "chargepath.demo", "--port", "$Port")
if ($UseIntegration) {
    $DemoArguments += @(
        "--mode", "integration",
        "--station-snapshot", $Snapshot.Snapshot,
        "--station-manifest", $Snapshot.Manifest,
        "--osrm-endpoint", "https://router.project-osrm.org",
        "--allow-remote-osrm",
        "--candidate-cap", "$PublicCandidateCap"
    )
    Write-Host "Using EPDK snapshot $($Snapshot.Date) and the public OSRM demo server."
}
if ($Python.FromSource) {
    $env:PYTHONPATH = Join-Path $RepoRoot "src"
}

$OutputLog = Join-Path $env:TEMP "chargepath-demo-$Port.out.log"
$ErrorLog = Join-Path $env:TEMP "chargepath-demo-$Port.err.log"
$QuotedArguments = $DemoArguments | ForEach-Object { '"' + $_ + '"' }
$Server = Start-Process -FilePath $Python.Executable -ArgumentList ($QuotedArguments -join " ") `
    -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $OutputLog -RedirectStandardError $ErrorLog

$Deadline = (Get-Date).AddSeconds($StartupSeconds)
$Ready = $false
while ((Get-Date) -lt $Deadline) {
    Start-Sleep -Milliseconds 250
    if (Test-ChargePathHealth) {
        $Ready = $true
        break
    }
    if ($Server.HasExited) {
        break
    }
}
if (-not $Ready) {
    Write-Host "ChargePath could not start at $AppUrl. Server output:"
    Get-Content -LiteralPath $ErrorLog -Tail 15 -ErrorAction SilentlyContinue | Write-Host
    exit 1
}

Open-ChargePath
