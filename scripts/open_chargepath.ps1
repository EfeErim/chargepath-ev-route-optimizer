param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8743
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ServerScript = Join-Path $PSScriptRoot "start_custom_map.ps1"
$AppUrl = "http://127.0.0.1:$Port/"
$HealthUrl = "${AppUrl}health"

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

if (-not (Test-ChargePathHealth)) {
    $QuotedServerScript = '"' + $ServerScript + '"'
    $ServerArguments = (
        "-NoProfile -ExecutionPolicy Bypass -File $QuotedServerScript " +
        "-UsePublicOsrm -Port $Port"
    )
    Start-Process -FilePath "powershell.exe" -ArgumentList $ServerArguments -WindowStyle Hidden

    $Ready = $false
    for ($Attempt = 0; $Attempt -lt 40; $Attempt++) {
        Start-Sleep -Milliseconds 250
        if (Test-ChargePathHealth) {
            $Ready = $true
            break
        }
    }
    if (-not $Ready) {
        throw "ChargePath v3 could not start at $AppUrl. Another or outdated process may own the port."
    }
}

Start-Process -FilePath $AppUrl
