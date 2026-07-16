param(
    [string]$EnvFile = "examples/server.simulation.env.example",
    [Nullable[int]]$MaxLoops = $null,
    [switch]$SkipPreflight
)

$ErrorActionPreference = "Stop"

if (!(Test-Path $EnvFile)) {
    throw "Env file not found: $EnvFile"
}

$args = @("scripts/run_live_sim_runtime.py", "--env-file", $EnvFile)
if ($null -ne $MaxLoops) {
    $args += @("--max-loops", $MaxLoops.ToString())
}
if ($SkipPreflight) {
    $args += "--skip-preflight"
}

python @args
