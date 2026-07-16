# RWUSD Strategy Capabilities

## Operating Model

The strategy is a continuous dry-run simulator for a Portfolio Margin hedge
workflow. It uses real market and account-derived inputs where configured, but
models execution locally instead of submitting exchange orders.

## Implemented Capabilities

### Market Selection

- Evaluates BTCUSDT, ETHUSDT, and SOLUSDT on each cycle.
- Scores liquidity, volatility, funding, margin capacity, and execution cost.
- Retains the current symbol until a switch threshold is exceeded, reducing
  unnecessary churn.

### Hedge and Execution Simulation

- Builds long/short hedge intents and sizing rules from exchange filters.
- Creates tick-level quote plans based on actual filled quantities.
- Detects a missing or imbalanced leg and plans only the corrective quantity.
- Simulates partial fills, minimum fill quantity, timeout, cancel, re-quote,
  and maximum re-quote limits.
- Marks risk-reduction orders as reduce-only in the simulation lifecycle.

### Risk Controls

- Soft and hard uniMMR thresholds.
- Maximum drawdown gate.
- Total absolute exposure, total net exposure, and single-symbol net exposure
  caps when configured.
- Pause and reduce-risk paths that block additional exposure.

### RWUSD Closed Loop

- Tracks simulated RWUSD principal and accrued interest.
- Accumulates eligible profit in a profit bucket.
- Plans a PM-to-Spot-to-RWUSD profit sweep when risk conditions allow it.
- Plans RWUSD-to-Spot-to-PM redemption for margin top-up when risk requires it.

### Reliability and Observability

- Persists hedge state, profit state, and dry-run order lifecycle snapshots in
  SQLite.
- Restores persisted state after restart.
- Produces structured runtime, selector, risk, and transfer events.
- Produces runtime summaries, snapshots, and comparison reports for long-run
  observation.

## Explicit Limitations

- No real order placement is enabled or represented as production-ready.
- The simulator's fill model is configurable but is not a substitute for live
  order-book execution, fees, slippage, latency, or liquidation modeling.
- Long-horizon profitability and robustness require out-of-sample dry-run data
  and cannot be inferred from a short observation window.
- RWUSD redemption sizing is a modeled control path and should be further
  calibrated against actual account mechanics before any production use.

## Recommended Validation Sequence

1. Run preflight with `LIVE_DRY_RUN=true`.
2. Run deterministic file-driven tests.
3. Run the continuous server-side simulator on real market inputs.
4. Analyze partial fills, timeouts, re-quotes, exposure drift, and risk events
   over a sufficiently long observation period.
5. Tune execution-model parameters only after collecting representative data.
