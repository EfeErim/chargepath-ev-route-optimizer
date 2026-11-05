# Shared helpers for the Windows demo launchers. Dot-source this file; it starts nothing itself.

function Resolve-ChargePathPython {
    param([Parameter(Mandatory)][string]$RepoRoot)

    $VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $VenvPython) {
        return @{ Executable = $VenvPython; FromSource = $false }
    }

    # The package has no runtime dependency, so any Python 3.11+ can run it from src/.
    # The real interpreter path is resolved because the py launcher misreads quoted version flags.
    $Candidates = @(
        @{ Executable = "py"; Arguments = @("-3.11") },
        @{ Executable = "py"; Arguments = @("-3") },
        @{ Executable = "python"; Arguments = @() }
    )
    $VersionCheck = (
        "import sys; print(sys.executable); " +
        "sys.exit(0 if sys.version_info >= (3, 11) else 1)"
    )
    foreach ($Candidate in $Candidates) {
        if (-not (Get-Command $Candidate.Executable -ErrorAction SilentlyContinue)) {
            continue
        }
        $PreviousPreference = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        try {
            $CandidateArguments = $Candidate.Arguments
            $Interpreter = & $Candidate.Executable @CandidateArguments -c $VersionCheck 2> $null
            if ($LASTEXITCODE -eq 0 -and $Interpreter) {
                return @{ Executable = "$Interpreter".Trim(); FromSource = $true }
            }
        }
        catch {
            continue
        }
        finally {
            $ErrorActionPreference = $PreviousPreference
        }
    }
    return $null
}

function Resolve-EpdkSnapshot {
    param([Parameter(Mandatory)][string]$RepoRoot)

    $RawRoot = Join-Path $RepoRoot "data\raw\epdk"
    if (-not (Test-Path -LiteralPath $RawRoot)) {
        return $null
    }
    $Dates = Get-ChildItem -LiteralPath $RawRoot -Directory | Sort-Object Name -Descending
    foreach ($Date in $Dates) {
        $Snapshot = Join-Path $Date.FullName "response.json"
        $Manifest = Join-Path $RepoRoot "data\processed\epdk\$($Date.Name)\manifest.json"
        if ((Test-Path -LiteralPath $Snapshot) -and (Test-Path -LiteralPath $Manifest)) {
            return @{ Date = $Date.Name; Snapshot = $Snapshot; Manifest = $Manifest }
        }
    }
    return $null
}

function Get-SnapshotHelp {
    return (
        "No local EPDK station snapshot was found. Real station data is not distributed with the " +
        "repository. To retrieve one from the official service, run: " +
        "python scripts/fetch_epdk_snapshot.py"
    )
}

function Get-PythonHelp {
    return (
        "Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/ " +
        "or create the project environment described in README.md."
    )
}
