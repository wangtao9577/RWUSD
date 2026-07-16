from decimal import Decimal
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.domain.enums import PositionSide
from src.domain.models import ProfitBucket
from src.domain.enums import StrategyPhase
from src.infra.persistence import SqliteStateStore
from src.portfolio.state import HedgeState
from src.strategy.dry_run_order_lifecycle import DryRunOrderLifecycle
from src.strategy.tick_quote_planner import QuoteOrder, TickQuotePlan


class SqliteStateStoreTests(unittest.TestCase):
    def test_save_and_load_hedge_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = SqliteStateStore(Path(tmp_dir) / "state.db")
            try:
                expected = HedgeState(
                    underlying_symbol="BTC",
                    symbol="BTCUSDT",
                    long_symbol="BTCUSDT",
                    short_symbol="BTCUSDC",
                    phase=StrategyPhase.OPENING_HEDGE,
                    long_notional=Decimal("1000"),
                    short_notional=Decimal("1000"),
                    long_filled=True,
                    short_filled=False,
                    sim_leverage=Decimal("33"),
                    sim_long_qty=Decimal("0.25"),
                    sim_short_qty=Decimal("0.30"),
                    sim_long_entry_price=Decimal("61000"),
                    sim_short_entry_price=Decimal("60950"),
                    sim_long_unrealized_pnl=Decimal("12.5"),
                    sim_short_unrealized_pnl=Decimal("-7.5"),
                    sim_last_mark_price=Decimal("61234.5"),
                    sim_take_profit_count=2,
                    sim_restore_count=1,
                    sim_cycle_id=7,
                    last_symbol_switch_minute=135,
                )

                store.save_hedge_state(expected)
                actual = store.load_hedge_state()

                self.assertEqual(actual, expected)
            finally:
                store.close()

    def test_save_and_load_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = SqliteStateStore(Path(tmp_dir) / "state.db")
            try:
                expected_bucket = ProfitBucket(
                    realized_pnl_total=Decimal("45.5"),
                    realized_pnl_available_for_deposit=Decimal("12.25"),
                    harvest_buffer=Decimal("9.75"),
                    rwusd_principal=Decimal("10123.45"),
                    rwusd_interest_accrued=Decimal("2.75"),
                    rwusd_redeemable=Decimal("10123.45"),
                    harvest_count=3,
                    deposit_count=4,
                    redeem_count=1,
                    closed_loop_ready=True,
                    last_rebalance_action="sweep",
                    sweep_block_reason="none",
                )

                store.save_runtime_state(
                    profit_bucket=expected_bucket,
                    pending_rebalance_side=PositionSide.SHORT,
                )
                actual = store.load_runtime_state()

                self.assertIsNotNone(actual)
                assert actual is not None
                self.assertEqual(actual["profit_bucket"], expected_bucket)
                self.assertEqual(actual["pending_rebalance_side"], PositionSide.SHORT)
            finally:
                store.close()

    def test_save_and_load_dry_run_order_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = SqliteStateStore(Path(tmp_dir) / "state.db")
            try:
                lifecycle = DryRunOrderLifecycle(long_quantity=Decimal("1"))
                order_id = lifecycle.replace_orders(
                    TickQuotePlan(
                        cancel_open_orders=True,
                        orders=(
                            QuoteOrder(
                                position_side=PositionSide.LONG,
                                side="BUY",
                                quantity=Decimal("2"),
                                price=Decimal("1800"),
                                reduce_only=False,
                            ),
                        ),
                        reason="open_long",
                    )
                ).created_order_ids[0]
                lifecycle.apply_execution(
                    order_id=order_id,
                    cumulative_filled_quantity=Decimal("0.5"),
                    status="PARTIALLY_FILLED",
                )

                store.save_dry_run_order_lifecycle(lifecycle.to_snapshot())
                restored_snapshot = store.load_dry_run_order_lifecycle()
                restored = DryRunOrderLifecycle.from_snapshot(restored_snapshot or {})

                self.assertEqual(restored.long_quantity, Decimal("1.5"))
                self.assertEqual(restored.get_order(order_id).status, "PARTIALLY_FILLED")
                self.assertEqual(
                    restored.get_order(order_id).cumulative_filled_quantity,
                    Decimal("0.5"),
                )
            finally:
                store.close()

    def test_load_runtime_state_adds_closed_loop_defaults_for_legacy_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "state.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE runtime_state (
                        singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                        pending_rebalance_side TEXT,
                        realized_pnl_total TEXT NOT NULL DEFAULT '0',
                        realized_pnl_available_for_deposit TEXT NOT NULL DEFAULT '0',
                        rwusd_principal TEXT NOT NULL DEFAULT '0',
                        rwusd_interest_accrued TEXT NOT NULL DEFAULT '0',
                        rwusd_redeemable TEXT NOT NULL DEFAULT '0',
                        harvest_count INTEGER NOT NULL DEFAULT 0,
                        deposit_count INTEGER NOT NULL DEFAULT 0,
                        redeem_count INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO runtime_state (
                        singleton_id,
                        pending_rebalance_side,
                        realized_pnl_total,
                        realized_pnl_available_for_deposit,
                        rwusd_principal,
                        rwusd_interest_accrued,
                        rwusd_redeemable,
                        harvest_count,
                        deposit_count,
                        redeem_count
                    ) VALUES (1, 'LONG', '8', '5', '3', '0.2', '3.2', 1, 2, 0)
                    """
                )
                connection.commit()
            finally:
                connection.close()

            store = SqliteStateStore(db_path)
            try:
                actual = store.load_runtime_state()

                self.assertIsNotNone(actual)
                assert actual is not None
                self.assertEqual(
                    actual["profit_bucket"],
                    ProfitBucket(
                        realized_pnl_total=Decimal("8"),
                        realized_pnl_available_for_deposit=Decimal("5"),
                        harvest_buffer=Decimal("0"),
                        rwusd_principal=Decimal("3"),
                        rwusd_interest_accrued=Decimal("0.2"),
                        rwusd_redeemable=Decimal("3.2"),
                        harvest_count=1,
                        deposit_count=2,
                        redeem_count=0,
                        closed_loop_ready=False,
                        last_rebalance_action=None,
                        sweep_block_reason=None,
                    ),
                )
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
