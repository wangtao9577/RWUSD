# Server Simulation Runbook

## Purpose

This runbook describes how to run the new server-side simulation runtime that:

- uses real account and market data inputs
- keeps execution in forced dry-run mode
- writes an isolated simulation state database
- emits a per-run JSONL log, runtime summary, and account-market snapshot

This is the bridge step before we deploy a longer-running live-vs-sim comparison harness.

## Files

- `examples/server.simulation.env.example`
- `scripts/run_live_sim_runtime.py`
- `scripts/run_live_sim_runtime.ps1`
- `scripts/compare_live_vs_sim.py`
- `scripts/run_server_sim_batch.py`
- `scripts/run_server_sim_daemon.py`
- `server_test_window/index.html`
- `docs/server-dry-run-runbook.md`

## Output Layout

By default each run writes into a timestamped directory:

- `tmp/simulation/YYYY-MM-DD/HHMMSS/live_sim_runtime.jsonl`
- `tmp/simulation/YYYY-MM-DD/HHMMSS/runtime-summary.json`
- `tmp/simulation/YYYY-MM-DD/HHMMSS/account-market-snapshot.json`

The simulation state database is isolated at:

- `tmp/simulation/live_state.db`

## Setup

1. Copy `examples/server.simulation.env.example` to a server-local file such as `.env.simulation`.
2. Fill in the real Binance API credentials.
3. Keep `LIVE_DRY_RUN=true`.
4. Keep candidate symbols limited to `BTCUSDT,ETHUSDT,SOLUSDT`.
5. Keep `LIVE_LOG_ROTATE_DAILY=false` so the batch command can read the fixed live log path.

## Local-Only Credential Entry

If the operator prefers browser-based credential entry for the simulation env file, start the local-only control server on the server host:

```bash
python scripts/run_sim_config_server.py --host 127.0.0.1 --port 18081 --env-file .env.simulation --page-dir server_sim_control
```

Then create an SSH tunnel from the local machine:

```bash
ssh -L 18081:127.0.0.1:18081 <user>@<server>
```

Open the forwarded page locally:

- `http://127.0.0.1:18081/`

This entry path is intentionally restricted:

- it is simulation-only
- it only updates `BINANCE_API_KEY` and `BINANCE_API_SECRET`
- it keeps `LIVE_LOG_PATH=tmp/server/live_runtime.jsonl`
- it does not modify the public `8081` monitor page
- it does not touch the existing DBRSI production accounts

## Standard Run

### Python entrypoint

```bash
python scripts/run_live_sim_runtime.py --env-file .env.simulation --max-loops 200
```

### PowerShell entrypoint

```powershell
powershell -File scripts/run_live_sim_runtime.ps1 -EnvFile .env.simulation -MaxLoops 200
```

Behavior:

- runs `live-preflight` first by default
- then starts `live-sim-runtime`
- forces dry-run execution through the simulation runtime builder
- writes grouped outputs under `tmp/simulation/...`

## Recommended Batch Entry Point

For server-side repeated sampling, prefer the batch orchestrator:

```bash
python scripts/run_server_sim_batch.py \
  --env-file .env.simulation \
  --live-log-path tmp/server/live_runtime.jsonl \
  --output-root tmp/server/sim_batches \
  --max-loops 100
```

This one command will:

- collect a live-side runtime summary
- collect a live-side account and market snapshot
- run one simulation batch
- build a live-vs-sim comparison report

Default batch layout:

- `tmp/server/sim_batches/YYYY-MM-DD/HHMMSS/live-runtime-summary.json`
- `tmp/server/sim_batches/YYYY-MM-DD/HHMMSS/live-account-market-snapshot.json`
- `tmp/server/sim_batches/YYYY-MM-DD/HHMMSS/simulation/live_sim_runtime.jsonl`
- `tmp/server/sim_batches/YYYY-MM-DD/HHMMSS/simulation/runtime-summary.json`
- `tmp/server/sim_batches/YYYY-MM-DD/HHMMSS/simulation/account-market-snapshot.json`
- `tmp/server/sim_batches/YYYY-MM-DD/HHMMSS/live-vs-sim-comparison.json`
- `tmp/server/sim_batches/latest.json`

Useful options:

- `--skip-live-snapshot`
- `--skip-sim-preflight`
- `--max-loops 50`

The batch script also writes `latest.json` under the batch root so a read-only static test window can always discover the newest run without guessing the timestamped directory.

## Optional Modes

Skip preflight only when you have already validated the environment in the same server session:

```bash
python scripts/run_live_sim_runtime.py --env-file .env.simulation --max-loops 50 --skip-preflight
```

## What To Check After Each Run

Minimum checklist:

- Did the selected symbol remain within `BTCUSDT`, `ETHUSDT`, and `SOLUSDT`?
- Did `runtime-summary.json` include sensible `selected_symbol_counts`?
- Did `rebalance_action_counts` show expected behavior for `restore_now`, `restore_later`, or `reduce_risk`?
- Did `profit_sweep_count` and `redeem_topup_count` stay consistent with the account condition?
- Does `account-market-snapshot.json` contain sane `uni_mmr`, `account_equity`, `available_balance`, and market rows for all three candidates?

## Compare Live Vs Simulation

Once you have:

- a live-side `runtime-summary.json`
- a live-side `account-market-snapshot.json`
- a simulation-side `runtime-summary.json`
- a simulation-side `account-market-snapshot.json`

you can build a direct comparison report:

```bash
python scripts/compare_live_vs_sim.py \
  --live-summary-path tmp/server/reports/live-runtime-summary.json \
  --sim-summary-path tmp/simulation/2026-06-29/120000/runtime-summary.json \
  --live-snapshot-path tmp/server/reports/live-account-market-snapshot.json \
  --sim-snapshot-path tmp/simulation/2026-06-29/120000/account-market-snapshot.json \
  --output-path tmp/server/reports/live-vs-sim-comparison.json
```

Expected report sections:

- `summary`
- `snapshot`
- `matches`
- `mismatches`

Most useful mismatch checks in this first version:

- `selected_symbol_counts`
- `rebalance_action_counts`
- `profit_sweep_count`
- `redeem_topup_count`
- `snapshot_selected_symbols`
- `snapshot_position_symbols`
- `snapshot_account_metrics`
- `snapshot_market_rows`

This first version is intentionally summary-oriented. It helps us quickly spot which batch windows need deeper event-level investigation.

## Separate Test Window

To avoid any impact on the existing DBRSI production window and its three running accounts:

1. use a separate `nginx` server block on a different port such as `8081`
2. serve `server_test_window/index.html` from a separate directory
3. expose only the batch output directory as static JSON data
4. do not proxy any account write endpoints into this window

Recommended server layout:

- static page root: `/opt/apps/project2-sim-window`
- batch data root: `/opt/apps/project2-sim-data`
- page URL: `http://45.77.247.235:8081/`

This window is meant to be read-only:

- it reads `latest.json`
- it loads `live-vs-sim-comparison.json`
- it links to the related live/simulation artifacts
- it does not call `/start`, `/stop`, `/close`, or any account mutation API
- credential entry must stay on the local-only control server instead of this page

## Recommended Server Usage

For the first phase, run it as a bounded batch job instead of a permanent daemon:

1. start the local-only credential server if browser-based credential entry is needed
2. use SSH forwarding and save the simulation credentials
3. execute `50` to `200` loops per batch
4. inspect the generated `live-vs-sim-comparison.json`
5. identify mismatch-heavy windows first
6. keep the output directories as the source dataset for the later comparison harness

## Scheduling Direction

This repository now provides the batch command, not the daemon scheduler itself.

Recommended server setup:

1. use `cron`, `systemd timer`, or Windows Task Scheduler
2. trigger `run_server_sim_batch.py` every `30` to `60` minutes
3. keep each timestamped batch directory for later mismatch review
4. add retention rules only after we have enough sample windows

## Continuous Observation Mode

When you want the RWUSD simulation window to stay up like a long-running strategy monitor, use the daemon wrapper instead of a short batch:

```bash
python scripts/run_server_sim_daemon.py \
  --env-file .env.simulation \
  --live-log-path tmp/server/live_runtime.jsonl \
  --output-root /opt/apps/project2-sim-data \
  --max-loops 100000 \
  --restart-delay-seconds 2 \
  --failure-backoff-seconds 10 \
  --failure-backoff-multiplier 2 \
  --max-failure-backoff-seconds 120
```

Behavior:

- keeps relaunching `run_server_sim_batch.py`
- preserves `latest.json` and `runner-state.json` generation through the batch entrypoint
- writes `supervisor-state.json` under the output root for daemon-level health
- uses short restart delay on normal completion
- uses exponential backoff after failures

For Linux servers you can also install:

- `ops/server_sim_window/run_project2_sim_forever.sh`
- `ops/server_sim_window/project2-sim-runner.service`

## Next Phase

After this step, the next layer is:

1. schedule repeated simulation batches on the server
2. retain each run directory as a time-window sample
3. compare simulated decisions with observed live runtime behavior
4. investigate mismatches by date, loop window, and selected symbol
