# RWUSD Hedge Strategy

A Python research and continuous dry-run system for a Binance Portfolio Margin
RWUSD yield-and-hedge strategy. It consumes real market and account inputs,
but the published configuration keeps execution in dry-run mode.

## What It Does

- Ranks `BTCUSDT`, `ETHUSDT`, and `SOLUSDT` using liquidity, volatility,
  funding, margin, and execution-cost inputs.
- Plans a two-sided hedge and maintains a neutral quote target from actual
  filled quantities.
- Models order submission, partial fills, timeout, cancellation, and re-quote
  behavior in dry-run mode.
- Persists hedge state and simulated order lifecycle state in SQLite so a
  restarted runtime can resume safely.
- Applies uniMMR, drawdown, and total/net/single-symbol exposure limits before
  permitting new risk.
- Tracks simulated RWUSD principal and accrued interest, sweeps eligible
  profits to RWUSD, and models RWUSD redemption for margin top-up.
- Emits structured JSONL runtime events and produces batch reports for
  observation and comparison.

See [strategy capabilities](docs/strategy-capabilities.md) for the supported
workflow and explicit limitations.

## Safety Boundary

This repository is for research and dry-run simulation. It does not contain
credentials, runtime databases, server data, or deployment secrets. Keep
`LIVE_DRY_RUN=true` while validating behavior. Do not treat it as a production
trading system or investment advice.

## Quick Start

Requires Python 3.11 or newer.

```bash
python -m venv .venv
.venv/Scripts/activate
pip install -e .
copy .env.example .env
python -m unittest discover -s tests/unit
```

Populate `.env` with your own credentials only for a controlled dry-run
environment. Start with preflight and a deterministic file-driven rehearsal:

```bash
python -m src.app.main live-preflight --env-file .env
python -m src.app.main live-runtime-file --env-file .env --cycle-inputs examples/live_cycle_inputs.smoke.json --max-loops 2
```

The market/account-driven simulation entry point is:

```bash
python -m src.app.main live-sim-runtime --env-file .env
```

## Repository Layout

- `src/`: strategy, risk, portfolio, exchange, persistence, and runtime code.
- `tests/`: unit, integration, and backtest coverage.
- `examples/`: credential-free sample configuration and cycle inputs.
- `scripts/`: local and server dry-run helpers.
- `ops/server_sim_window/`: systemd-oriented continuous simulation runner.
- `docs/`: strategy scope, gap audit, and server operating runbooks.

## Validation

The focused order lifecycle, quote planning, risk, and persistence suite can
be run with:

```bash
python -m unittest tests/unit/test_tick_quote_planner.py tests/unit/test_dry_run_order_lifecycle.py tests/unit/test_risk_rules.py tests/unit/test_persistence.py
```

## Current Status

The strategy is deployed as a continuous server-side dry-run. Its core
simulation loop, persistence, order lifecycle model, risk gates, and RWUSD
profit/redeem paths are implemented. Real exchange order placement is not a
release goal for this version.
