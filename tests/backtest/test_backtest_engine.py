from decimal import Decimal
import unittest

from src.backtest.engine import BacktestEngine, BacktestRuntimeState
from src.domain.enums import PositionSide, StrategyPhase
from src.domain.models import ProfitBucket
from src.market.selector import SymbolSelector


class BacktestEngineTests(unittest.TestCase):
    def test_backtest_engine_emits_selected_symbol_history(self) -> None:
        engine = BacktestEngine()
        rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.80"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.85"),
                "blocked": False,
            },
            {
                "symbol": "ETHUSDT",
                "close": Decimal("3000"),
                "liquidity": Decimal("0.80"),
                "volatility": Decimal("0.70"),
                "funding": Decimal("0.90"),
                "margin": Decimal("0.80"),
                "blocked": False,
            },
            {
                "symbol": "SOLUSDT",
                "close": Decimal("150"),
                "liquidity": Decimal("0.70"),
                "volatility": Decimal("0.60"),
                "funding": Decimal("0.95"),
                "margin": Decimal("0.75"),
                "blocked": False,
            },
        ]

        result = engine.run(rows=rows)

        self.assertEqual(result.selected_symbols, ["BTCUSDT"])

    def test_backtest_engine_skips_blocked_top_symbol(self) -> None:
        engine = BacktestEngine()
        rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.95"),
                "volatility": Decimal("0.85"),
                "funding": Decimal("0.85"),
                "margin": Decimal("0.90"),
                "blocked": True,
            },
            {
                "symbol": "ETHUSDT",
                "close": Decimal("3000"),
                "liquidity": Decimal("0.80"),
                "volatility": Decimal("0.70"),
                "funding": Decimal("0.90"),
                "margin": Decimal("0.80"),
                "blocked": False,
            },
        ]

        result = engine.run(rows=rows)

        self.assertEqual(result.selected_symbols, ["ETHUSDT"])

    def test_backtest_run_keeps_current_symbol_between_eval_checkpoints(self) -> None:
        engine = BacktestEngine()
        rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.60"),
                "volatility": Decimal("0.60"),
                "funding": Decimal("0.60"),
                "margin": Decimal("0.60"),
                "blocked": False,
            },
            {
                "symbol": "ETHUSDT",
                "close": Decimal("3000"),
                "liquidity": Decimal("1.00"),
                "volatility": Decimal("1.00"),
                "funding": Decimal("1.00"),
                "margin": Decimal("1.00"),
                "blocked": False,
            },
        ]

        result = engine.run(
            rows=rows,
            current_symbol="BTCUSDT",
            current_phase=StrategyPhase.HEDGED,
            minute_of_day=7,
        )

        self.assertEqual(result.selected_symbols, ["BTCUSDT"])
        self.assertEqual(result.current_symbol, "BTCUSDT")
        self.assertEqual(result.phase, StrategyPhase.HEDGED)

    def test_backtest_run_holds_current_symbol_while_rebalancing_missing_leg(self) -> None:
        engine = BacktestEngine()
        rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.60"),
                "volatility": Decimal("0.60"),
                "funding": Decimal("0.60"),
                "margin": Decimal("0.60"),
                "blocked": False,
                "long_unrealized": Decimal("5"),
                "short_unrealized": Decimal("-2"),
            },
            {
                "symbol": "ETHUSDT",
                "close": Decimal("3000"),
                "liquidity": Decimal("1.00"),
                "volatility": Decimal("1.00"),
                "funding": Decimal("1.00"),
                "margin": Decimal("1.00"),
                "blocked": False,
                "long_unrealized": Decimal("5"),
                "short_unrealized": Decimal("-2"),
            },
        ]

        result = engine.run(
            rows=rows,
            snapshot={
                "account_equity": Decimal("1000"),
                "available_balance": Decimal("500"),
                "uni_mmr": Decimal("12"),
            },
            runtime_state=BacktestRuntimeState(
                current_symbol="BTCUSDT",
                phase=StrategyPhase.REBALANCING,
                pending_rebalance_side=PositionSide.LONG,
                last_symbol_switch_minute=0,
            ),
            minute_of_day=120,
            allow_restore_now=False,
        )

        self.assertEqual(result.selected_symbols, ["BTCUSDT"])
        self.assertEqual(result.current_symbol, "BTCUSDT")
        self.assertEqual(result.phase, StrategyPhase.REBALANCING)
        self.assertEqual(result.pending_rebalance_side, PositionSide.LONG)
        self.assertIsNotNone(result.runtime_state)
        self.assertEqual(result.runtime_state.current_symbol, "BTCUSDT")
        self.assertEqual(result.runtime_state.phase, StrategyPhase.REBALANCING)
        self.assertEqual(
            result.runtime_state.pending_rebalance_side,
            PositionSide.LONG,
        )

    def test_backtest_run_holds_current_symbol_while_hedged_position_is_active(self) -> None:
        engine = BacktestEngine()
        rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.60"),
                "volatility": Decimal("0.60"),
                "funding": Decimal("0.60"),
                "margin": Decimal("0.60"),
                "blocked": False,
                "long_unrealized": Decimal("5"),
                "short_unrealized": Decimal("-2"),
            },
            {
                "symbol": "ETHUSDT",
                "close": Decimal("3000"),
                "liquidity": Decimal("1.00"),
                "volatility": Decimal("1.00"),
                "funding": Decimal("1.00"),
                "margin": Decimal("1.00"),
                "blocked": False,
                "long_unrealized": Decimal("5"),
                "short_unrealized": Decimal("-2"),
            },
        ]

        result = engine.run(
            rows=rows,
            snapshot={
                "account_equity": Decimal("1000"),
                "available_balance": Decimal("500"),
                "uni_mmr": Decimal("12"),
            },
            runtime_state=BacktestRuntimeState(
                current_symbol="BTCUSDT",
                phase=StrategyPhase.HEDGED,
                last_symbol_switch_minute=0,
            ),
            minute_of_day=120,
        )

        self.assertEqual(result.selected_symbols, ["BTCUSDT"])
        self.assertEqual(result.current_symbol, "BTCUSDT")
        self.assertEqual(result.phase, StrategyPhase.HEDGED)
        self.assertIsNotNone(result.runtime_state)
        self.assertEqual(result.runtime_state.current_symbol, "BTCUSDT")
        self.assertEqual(result.runtime_state.phase, StrategyPhase.HEDGED)

    def test_backtest_run_holds_current_symbol_while_opening_hedge_is_active(self) -> None:
        engine = BacktestEngine()
        rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.60"),
                "volatility": Decimal("0.60"),
                "funding": Decimal("0.60"),
                "margin": Decimal("0.60"),
                "blocked": False,
            },
            {
                "symbol": "ETHUSDT",
                "close": Decimal("3000"),
                "liquidity": Decimal("1.00"),
                "volatility": Decimal("1.00"),
                "funding": Decimal("1.00"),
                "margin": Decimal("1.00"),
                "blocked": False,
            },
        ]

        result = engine.run(
            rows=rows,
            snapshot={
                "account_equity": Decimal("1000"),
                "available_balance": Decimal("500"),
                "uni_mmr": Decimal("12"),
            },
            runtime_state=BacktestRuntimeState(
                current_symbol="BTCUSDT",
                phase=StrategyPhase.OPENING_HEDGE,
                last_symbol_switch_minute=0,
            ),
            minute_of_day=120,
        )

        self.assertEqual(result.selected_symbols, ["BTCUSDT"])
        self.assertEqual(result.current_symbol, "BTCUSDT")
        self.assertEqual(result.phase, StrategyPhase.OPENING_HEDGE)
        self.assertIsNotNone(result.runtime_state)
        self.assertEqual(result.runtime_state.current_symbol, "BTCUSDT")
        self.assertEqual(result.runtime_state.phase, StrategyPhase.OPENING_HEDGE)

    def test_backtest_run_holds_current_symbol_while_taking_profit_is_active(self) -> None:
        engine = BacktestEngine()
        rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.60"),
                "volatility": Decimal("0.60"),
                "funding": Decimal("0.60"),
                "margin": Decimal("0.60"),
                "blocked": False,
                "long_unrealized": Decimal("30"),
                "short_unrealized": Decimal("-8"),
            },
            {
                "symbol": "ETHUSDT",
                "close": Decimal("3000"),
                "liquidity": Decimal("1.00"),
                "volatility": Decimal("1.00"),
                "funding": Decimal("1.00"),
                "margin": Decimal("1.00"),
                "blocked": False,
                "long_unrealized": Decimal("5"),
                "short_unrealized": Decimal("-2"),
            },
        ]

        result = engine.run(
            rows=rows,
            snapshot={
                "account_equity": Decimal("1000"),
                "available_balance": Decimal("500"),
                "uni_mmr": Decimal("12"),
            },
            runtime_state=BacktestRuntimeState(
                current_symbol="BTCUSDT",
                phase=StrategyPhase.TAKING_PROFIT,
                last_symbol_switch_minute=0,
            ),
            minute_of_day=120,
        )

        self.assertEqual(result.selected_symbols, ["BTCUSDT"])
        self.assertEqual(result.current_symbol, "BTCUSDT")
        self.assertEqual(result.phase, StrategyPhase.TAKING_PROFIT)
        self.assertEqual(result.take_profit_count, 0)
        self.assertIsNotNone(result.runtime_state)
        self.assertEqual(result.runtime_state.current_symbol, "BTCUSDT")
        self.assertEqual(result.runtime_state.phase, StrategyPhase.TAKING_PROFIT)

    def test_backtest_run_accepts_runtime_state_and_keeps_compatibility(self) -> None:
        engine = BacktestEngine()
        rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.60"),
                "volatility": Decimal("0.60"),
                "funding": Decimal("0.60"),
                "margin": Decimal("0.60"),
                "blocked": False,
            },
            {
                "symbol": "ETHUSDT",
                "close": Decimal("3000"),
                "liquidity": Decimal("1.00"),
                "volatility": Decimal("1.00"),
                "funding": Decimal("1.00"),
                "margin": Decimal("1.00"),
                "blocked": False,
            },
        ]
        legacy_result = engine.run(
            rows=rows,
            bucket={"rwusd_principal": Decimal("80"), "rwusd_redeemable": Decimal("80")},
            current_symbol="BTCUSDT",
            current_phase=StrategyPhase.REBALANCING,
            pending_rebalance_side=PositionSide.LONG,
            last_switch_minute=0,
            allow_restore_now=False,
            minute_of_day=7,
        )

        runtime_state_result = engine.run(
            rows=rows,
            runtime_state=BacktestRuntimeState(
                current_symbol="BTCUSDT",
                phase=StrategyPhase.REBALANCING,
                pending_rebalance_side=PositionSide.LONG,
                last_symbol_switch_minute=0,
                profit_bucket=ProfitBucket(
                    rwusd_principal=Decimal("80"),
                    rwusd_redeemable=Decimal("80"),
                ),
            ),
            allow_restore_now=False,
            minute_of_day=7,
        )

        self.assertEqual(runtime_state_result.selected_symbols, legacy_result.selected_symbols)
        self.assertEqual(runtime_state_result.current_symbol, legacy_result.current_symbol)
        self.assertEqual(runtime_state_result.phase, legacy_result.phase)
        self.assertEqual(
            runtime_state_result.pending_rebalance_side,
            legacy_result.pending_rebalance_side,
        )
        self.assertEqual(
            runtime_state_result.last_symbol_switch_minute,
            legacy_result.last_symbol_switch_minute,
        )
        self.assertEqual(runtime_state_result.rwusd_principal, Decimal("80"))
        self.assertEqual(
            runtime_state_result.rebalance_action_counts,
            legacy_result.rebalance_action_counts,
        )

    def test_backtest_run_sequence_holds_current_symbol_during_switch_cooldown_window(self) -> None:
        engine = BacktestEngine()
        engine._selector = SymbolSelector(
            switch_threshold=Decimal("0.20"),
            eval_interval_minutes=15,
            switch_cooldown_minutes=30,
        )
        first_rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("1.00"),
                "volatility": Decimal("1.00"),
                "funding": Decimal("1.00"),
                "margin": Decimal("1.00"),
                "blocked": False,
            },
            {
                "symbol": "ETHUSDT",
                "close": Decimal("3000"),
                "liquidity": Decimal("0.60"),
                "volatility": Decimal("0.60"),
                "funding": Decimal("0.60"),
                "margin": Decimal("0.60"),
                "blocked": False,
            },
        ]
        second_rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60020"),
                "liquidity": Decimal("0.60"),
                "volatility": Decimal("0.60"),
                "funding": Decimal("0.60"),
                "margin": Decimal("0.60"),
                "blocked": False,
            },
            {
                "symbol": "ETHUSDT",
                "close": Decimal("3010"),
                "liquidity": Decimal("1.00"),
                "volatility": Decimal("1.00"),
                "funding": Decimal("1.00"),
                "margin": Decimal("1.00"),
                "blocked": False,
            },
        ]
        healthy_snapshot = {
            "account_equity": Decimal("1000"),
            "available_balance": Decimal("500"),
            "uni_mmr": Decimal("12"),
        }

        result = engine.run_sequence(
            row_batches=[first_rows, second_rows],
            snapshot_batches=[healthy_snapshot, healthy_snapshot],
            minute_of_day_batches=[0, 15],
        )

        self.assertEqual(result.selected_symbols, ["BTCUSDT", "BTCUSDT"])
        self.assertEqual(result.current_symbol, "BTCUSDT")

    def test_backtest_run_sequence_returns_runtime_state_for_next_cycle(self) -> None:
        engine = BacktestEngine()
        rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.80"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.85"),
                "blocked": False,
                "long_unrealized": Decimal("30"),
                "short_unrealized": Decimal("-8"),
            }
        ]
        healthy_snapshot = {
            "account_equity": Decimal("1000"),
            "available_balance": Decimal("500"),
            "uni_mmr": Decimal("12"),
        }

        result = engine.run_sequence(
            row_batches=[rows],
            snapshot_batches=[healthy_snapshot],
            allow_restore_now_batches=[False],
            minute_of_day_batches=[0],
        )

        self.assertIsNotNone(result.runtime_state)
        self.assertEqual(result.runtime_state.current_symbol, "BTCUSDT")
        self.assertEqual(result.runtime_state.phase, StrategyPhase.REBALANCING)
        self.assertEqual(
            result.runtime_state.pending_rebalance_side,
            PositionSide.LONG,
        )
        self.assertEqual(result.runtime_state.last_symbol_switch_minute, 0)
        self.assertEqual(
            result.runtime_state.profit_bucket.realized_pnl_total,
            Decimal("28"),
        )
        self.assertEqual(result.current_symbol, result.runtime_state.current_symbol)
        self.assertEqual(result.phase, result.runtime_state.phase)

    def test_backtest_run_sequence_returns_simulated_execution_state_in_runtime_state(self) -> None:
        engine = BacktestEngine()
        rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.80"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.85"),
                "blocked": False,
                "long_unrealized": Decimal("30"),
                "short_unrealized": Decimal("-8"),
            }
        ]
        healthy_snapshot = {
            "account_equity": Decimal("1000"),
            "available_balance": Decimal("500"),
            "uni_mmr": Decimal("12"),
        }

        result = engine.run_sequence(
            row_batches=[rows],
            snapshot_batches=[healthy_snapshot],
            minute_of_day_batches=[0],
        )

        self.assertIsNotNone(result.runtime_state)
        self.assertGreater(result.runtime_state.sim_long_qty, Decimal("0"))
        self.assertGreater(result.runtime_state.sim_short_qty, Decimal("0"))
        self.assertEqual(result.runtime_state.sim_take_profit_count, 1)
        self.assertEqual(result.runtime_state.sim_restore_count, 1)
        self.assertEqual(result.runtime_state.sim_last_mark_price, Decimal("60000"))

    def test_backtest_counts_take_profit_and_restore_now_events(self) -> None:
        engine = BacktestEngine()
        rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.80"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.85"),
                "blocked": False,
                "long_unrealized": Decimal("30"),
                "short_unrealized": Decimal("-8"),
            }
        ]

        result = engine.run(
            rows=rows,
            snapshot={
                "account_equity": Decimal("1000"),
                "available_balance": Decimal("500"),
                "uni_mmr": Decimal("12"),
            },
        )

        self.assertEqual(result.selected_symbols, ["BTCUSDT"])
        self.assertEqual(result.realized_pnl, Decimal("28"))
        self.assertEqual(result.take_profit_count, 1)
        self.assertEqual(result.restore_now_count, 1)
        self.assertEqual(result.restore_later_count, 0)
        self.assertEqual(result.reduce_risk_count, 0)
        self.assertEqual(result.rebalance_action_counts, {"restore_now": 1})
        self.assertEqual(result.profit_sweep_count, 0)
        self.assertEqual(result.redeem_count, 0)

    def test_backtest_counts_take_profit_and_restore_later_events_in_bull_mode(self) -> None:
        engine = BacktestEngine()
        rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.80"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.85"),
                "blocked": False,
                "long_unrealized": Decimal("30"),
                "short_unrealized": Decimal("-8"),
            }
        ]

        result = engine.run(
            rows=rows,
            snapshot={
                "account_equity": Decimal("1000"),
                "available_balance": Decimal("500"),
                "uni_mmr": Decimal("12"),
            },
            bull_mode=True,
        )

        self.assertEqual(result.take_profit_count, 1)
        self.assertEqual(result.restore_now_count, 0)
        self.assertEqual(result.restore_later_count, 1)
        self.assertEqual(result.reduce_risk_count, 0)
        self.assertEqual(result.rebalance_action_counts, {"restore_later": 1})

    def test_backtest_counts_take_profit_and_reduce_risk_events_when_snapshot_is_stressed(self) -> None:
        engine = BacktestEngine()
        rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.80"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.85"),
                "blocked": False,
                "long_unrealized": Decimal("30"),
                "short_unrealized": Decimal("-8"),
            }
        ]

        result = engine.run(
            rows=rows,
            snapshot={
                "account_equity": Decimal("1000"),
                "available_balance": Decimal("280"),
                "uni_mmr": Decimal("12"),
            },
            current_drawdown=Decimal("0.20"),
        )

        self.assertEqual(result.take_profit_count, 1)
        self.assertEqual(result.restore_now_count, 0)
        self.assertEqual(result.restore_later_count, 0)
        self.assertEqual(result.reduce_risk_count, 1)
        self.assertEqual(result.rebalance_action_counts, {"reduce_risk": 1})

    def test_backtest_counts_redeem_events_when_snapshot_is_stressed(self) -> None:
        engine = BacktestEngine()
        rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.80"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.85"),
                "blocked": False,
            }
        ]

        result = engine.run(
            rows=rows,
            snapshot={
                "account_equity": Decimal("1000"),
                "available_balance": Decimal("20"),
                "uni_mmr": Decimal("5.5"),
            },
            bucket={
                "rwusd_principal": Decimal("80"),
                "rwusd_redeemable": Decimal("80"),
            },
        )

        self.assertEqual(result.selected_symbols, ["BTCUSDT"])
        self.assertEqual(result.redeem_count, 1)
        self.assertEqual(result.take_profit_count, 0)
        self.assertEqual(result.restore_now_count, 0)
        self.assertEqual(result.profit_sweep_count, 0)

    def test_backtest_counts_profit_sweep_events_when_bucket_is_sweepable(self) -> None:
        engine = BacktestEngine()
        rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.80"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.85"),
                "blocked": False,
            }
        ]

        result = engine.run(
            rows=rows,
            snapshot={
                "account_equity": Decimal("1000"),
                "available_balance": Decimal("500"),
                "uni_mmr": Decimal("12"),
            },
            bucket={
                "realized_pnl_total": Decimal("120"),
                "realized_pnl_available_for_deposit": Decimal("120"),
                "harvest_buffer": Decimal("120"),
                "closed_loop_ready": True,
            },
        )

        self.assertEqual(result.selected_symbols, ["BTCUSDT"])
        self.assertEqual(result.profit_sweep_count, 1)
        self.assertEqual(result.redeem_count, 0)

    def test_backtest_records_net_pnl_after_harvest_costs(self) -> None:
        engine = BacktestEngine()
        rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.80"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.85"),
                "blocked": False,
                "long_unrealized": Decimal("30"),
                "short_unrealized": Decimal("-8"),
                "recent_funding_cost": Decimal("1.2"),
            }
        ]

        result = engine.run(
            rows=rows,
            snapshot={
                "account_equity": Decimal("1000"),
                "available_balance": Decimal("500"),
                "uni_mmr": Decimal("12"),
            },
        )

        self.assertEqual(result.take_profit_count, 1)
        self.assertEqual(result.realized_pnl, Decimal("26.8"))
        self.assertEqual(result.bucket.realized_pnl_total, Decimal("26.8"))
        self.assertEqual(
            result.bucket.realized_pnl_available_for_deposit,
            Decimal("26.8"),
        )
        self.assertEqual(result.rwusd_principal, Decimal("0"))
        self.assertEqual(result.rwusd_interest_accrued, Decimal("0"))

    def test_backtest_repeated_run_uses_fresh_hedge_state(self) -> None:
        engine = BacktestEngine()
        rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.80"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.85"),
                "blocked": False,
                "long_unrealized": Decimal("30"),
                "short_unrealized": Decimal("-8"),
            }
        ]
        snapshot = {
            "account_equity": Decimal("1000"),
            "available_balance": Decimal("500"),
            "uni_mmr": Decimal("12"),
        }

        first_result = engine.run(rows=rows, snapshot=snapshot)

        self.assertEqual(first_result.take_profit_count, 1)
        self.assertFalse(hasattr(engine, "_hedge_engine"))

        second_result = engine.run(rows=rows, snapshot=snapshot)

        self.assertEqual(second_result.take_profit_count, 1)
        self.assertEqual(second_result.realized_pnl, Decimal("28"))
        self.assertEqual(second_result.rebalance_action_counts, {"restore_now": 1})

    def test_backtest_accrues_rwusd_interest_after_profit_is_deposited(self) -> None:
        engine = BacktestEngine()
        first_rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.80"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.85"),
                "blocked": False,
                "long_unrealized": Decimal("60"),
                "short_unrealized": Decimal("-8"),
            }
        ]
        second_rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60100"),
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.80"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.85"),
                "blocked": False,
                "long_unrealized": Decimal("0"),
                "short_unrealized": Decimal("0"),
            }
        ]

        result = engine.run_sequence(
            row_batches=[first_rows, second_rows],
            snapshot_batches=[
                {
                    "account_equity": Decimal("1000"),
                    "available_balance": Decimal("500"),
                    "uni_mmr": Decimal("12"),
                },
                {
                    "account_equity": Decimal("1000"),
                    "available_balance": Decimal("500"),
                    "uni_mmr": Decimal("12"),
                },
            ],
            elapsed_hours=Decimal("24"),
        )

        self.assertEqual(result.take_profit_count, 1)
        self.assertEqual(result.profit_sweep_count, 1)
        self.assertEqual(result.rwusd_principal, Decimal("58"))
        self.assertGreater(result.rwusd_interest_accrued, Decimal("0"))
        self.assertEqual(result.bucket.realized_pnl_total, Decimal("58"))
        self.assertEqual(
            result.bucket.realized_pnl_available_for_deposit,
            Decimal("0"),
        )
        self.assertEqual(result.bucket.rwusd_principal, Decimal("58"))
        self.assertEqual(
            result.bucket.rwusd_interest_accrued,
            result.rwusd_interest_accrued,
        )

    def test_backtest_sweeps_in_same_cycle_after_restore_now_returns_to_hedged(self) -> None:
        engine = BacktestEngine()
        rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.80"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.85"),
                "blocked": False,
                "long_unrealized": Decimal("60"),
                "short_unrealized": Decimal("-8"),
            }
        ]

        result = engine.run(
            rows=rows,
            snapshot={
                "account_equity": Decimal("1000"),
                "available_balance": Decimal("500"),
                "uni_mmr": Decimal("12"),
            },
        )

        self.assertEqual(result.take_profit_count, 1)
        self.assertEqual(result.restore_now_count, 1)
        self.assertEqual(result.profit_sweep_count, 1)
        self.assertEqual(result.bucket.harvest_buffer, Decimal("0"))
        self.assertEqual(result.phase, StrategyPhase.HEDGED)

    def test_backtest_keeps_profit_in_buffer_when_restore_later_leaves_rebalancing_open(self) -> None:
        engine = BacktestEngine()
        first_rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.80"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.85"),
                "blocked": False,
                "long_unrealized": Decimal("30"),
                "short_unrealized": Decimal("-8"),
            }
        ]
        second_rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60020"),
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.60"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.85"),
                "blocked": False,
                "long_unrealized": Decimal("5"),
                "short_unrealized": Decimal("-2"),
            }
        ]
        healthy_snapshot = {
            "account_equity": Decimal("1000"),
            "available_balance": Decimal("500"),
            "uni_mmr": Decimal("12"),
        }

        result = engine.run_sequence(
            row_batches=[first_rows, second_rows],
            snapshot_batches=[healthy_snapshot, healthy_snapshot],
            allow_restore_now_batches=[False, False],
        )

        self.assertEqual(result.take_profit_count, 1)
        self.assertEqual(result.restore_later_count, 2)
        self.assertEqual(result.profit_sweep_count, 0)
        self.assertEqual(result.bucket.harvest_buffer, Decimal("28"))
        self.assertEqual(result.pending_rebalance_side, PositionSide.LONG)

    def test_backtest_sequence_continues_rebalancing_after_restore_later(self) -> None:
        engine = BacktestEngine()
        first_rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.80"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.85"),
                "blocked": False,
                "long_unrealized": Decimal("30"),
                "short_unrealized": Decimal("-8"),
            }
        ]
        second_rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60020"),
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.60"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.85"),
                "blocked": False,
                "long_unrealized": Decimal("5"),
                "short_unrealized": Decimal("-2"),
            }
        ]
        healthy_snapshot = {
            "account_equity": Decimal("1000"),
            "available_balance": Decimal("500"),
            "uni_mmr": Decimal("12"),
        }

        result = engine.run_sequence(
            row_batches=[first_rows, second_rows],
            snapshot_batches=[healthy_snapshot, healthy_snapshot],
            allow_restore_now_batches=[False, True],
        )

        self.assertEqual(result.selected_symbols, ["BTCUSDT", "BTCUSDT"])
        self.assertEqual(result.take_profit_count, 1)
        self.assertEqual(result.restore_later_count, 1)
        self.assertEqual(result.restore_now_count, 1)
        self.assertEqual(
            result.rebalance_action_counts,
            {"restore_later": 1, "restore_now": 1},
        )
        self.assertEqual(result.realized_pnl, Decimal("28"))

    def test_backtest_sequence_restores_missing_leg_at_eval_checkpoint_from_minutes(self) -> None:
        engine = BacktestEngine()
        first_rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.80"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.85"),
                "blocked": False,
                "long_unrealized": Decimal("30"),
                "short_unrealized": Decimal("-8"),
            }
        ]
        second_rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60020"),
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.60"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.85"),
                "blocked": False,
                "long_unrealized": Decimal("5"),
                "short_unrealized": Decimal("-2"),
            }
        ]
        healthy_snapshot = {
            "account_equity": Decimal("1000"),
            "available_balance": Decimal("500"),
            "uni_mmr": Decimal("12"),
        }

        result = engine.run_sequence(
            row_batches=[first_rows, second_rows],
            snapshot_batches=[healthy_snapshot, healthy_snapshot],
            minute_of_day_batches=[1, 15],
        )

        self.assertEqual(result.selected_symbols, ["BTCUSDT", "BTCUSDT"])
        self.assertEqual(result.take_profit_count, 1)
        self.assertEqual(result.restore_later_count, 1)
        self.assertEqual(result.restore_now_count, 1)
        self.assertEqual(
            result.rebalance_action_counts,
            {"restore_later": 1, "restore_now": 1},
        )

    def test_backtest_can_simulate_multiple_active_symbols_in_one_cycle(self) -> None:
        engine = BacktestEngine(max_active_symbols=2)
        rows = [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("60000"),
                "liquidity": Decimal("0.95"),
                "volatility": Decimal("0.90"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.92"),
                "blocked": False,
                "long_unrealized": Decimal("30"),
                "short_unrealized": Decimal("-8"),
            },
            {
                "symbol": "ETHUSDT",
                "close": Decimal("3000"),
                "liquidity": Decimal("0.94"),
                "volatility": Decimal("0.89"),
                "funding": Decimal("0.69"),
                "margin": Decimal("0.91"),
                "blocked": False,
                "long_unrealized": Decimal("30"),
                "short_unrealized": Decimal("-8"),
            },
            {
                "symbol": "SOLUSDT",
                "close": Decimal("150"),
                "liquidity": Decimal("0.80"),
                "volatility": Decimal("0.70"),
                "funding": Decimal("0.60"),
                "margin": Decimal("0.75"),
                "blocked": False,
                "long_unrealized": Decimal("10"),
                "short_unrealized": Decimal("0"),
            },
        ]

        result = engine.run(
            rows=rows,
            snapshot={
                "account_equity": Decimal("1000"),
                "available_balance": Decimal("500"),
                "uni_mmr": Decimal("12"),
            },
        )

        self.assertEqual(result.selected_symbols, ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(result.take_profit_count, 2)
        self.assertEqual(result.restore_now_count, 2)
        self.assertEqual(result.rebalance_action_counts, {"restore_now": 2})
        self.assertEqual(result.realized_pnl, Decimal("56"))


if __name__ == "__main__":
    unittest.main()
