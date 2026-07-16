# Server Dry-Run Runbook

## Purpose

This runbook prepares the strategy for a server-side dry-run that stays aligned with the current live architecture while keeping `LIVE_DRY_RUN=true`.

## Files

- `examples/server.dryrun.env.example`
- `scripts/run_live_dryrun.ps1`
- `examples/live_cycle_inputs.smoke.json`

## Recommended Server Layout

Use a workspace layout like:

- `project2/`
- `project2/tmp/server/`
- `project2/tmp/server/live_runtime.jsonl`

## Setup

1. Copy `examples/server.dryrun.env.example` to a server-local env file such as `.env.server`.
2. Fill in real API credentials.
3. Keep `LIVE_DRY_RUN=true`.
4. Keep `LIVE_LOG_PATH=tmp/server/live_runtime.jsonl` or another server-local JSONL path.

## Rehearsal Before Long Run

Run local-style rehearsal first:

```bash
python -m src.app.main live-preflight --env-file .env.server
python -m src.app.main live-runtime-file --env-file .env.server --cycle-inputs examples/live_cycle_inputs.smoke.json --max-loops 2
```

Expected signals:

- `runtime.startup_completed`
- `live.selector_snapshot`
- `live.risk_decision` when risk blocks or reduces
- `runtime.loop_completed`

## Server Dry-Run Start

PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_live_dryrun.ps1 -EnvFile ".env.server" -MaxLoops 200
```

Direct command form:

```bash
python -m src.app.main live-preflight --env-file .env.server
python -m src.app.main live-runtime --env-file .env.server --max-loops 200
```

## What To Compare

During the server dry-run, keep collecting:

- JSONL event log from `LIVE_LOG_PATH`
- PM account snapshots
- current selected symbol by loop
- risk reasons by loop
- runtime loop counts and retries

Minimum comparison checklist:

- Did selector keep rotating only within `BTCUSDT/ETHUSDT/SOLUSDT`?
- Did any loop produce unexpected `risk_reasons`?
- Did runtime start cleanly after preflight?
- Did reconciliation remain clean?
- Did retry frequency stay low and understandable?

## Next Stage After Dry-Run

After this dry-run is stable, the next layer is a comparison harness:

1. Capture market/account inputs on a schedule.
2. Replay the same inputs into simulation.
3. Compare simulated decisions with observed live-account state.
4. Investigate mismatches by loop and event timestamp.
