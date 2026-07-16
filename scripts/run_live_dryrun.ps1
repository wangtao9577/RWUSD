param(
    [string]$EnvFile = "examples/server.dryrun.env.example",
    [int]$MaxLoops = 20
)

$ErrorActionPreference = "Stop"

if (!(Test-Path $EnvFile)) {
    throw "Env file not found: $EnvFile"
}

Write-Host "[1/2] Running preflight with $EnvFile"
python -m src.app.main live-preflight --env-file $EnvFile

Write-Host "[2/2] Starting live dry-run runtime"
python -m src.app.main live-runtime --env-file $EnvFile --max-loops $MaxLoops
