from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from src.app.live_runner import LiveRunner
from src.infra.persistence import SqliteStateStore
from src.strategy.dry_run_order_lifecycle import DryRunMatchingEngine


class LiveRunnerDryRunLifecycleTests(unittest.TestCase):
    def test_restores_partial_fill_and_continues_with_risk_reduction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "state.db"
            first_store = SqliteStateStore(state_path)
            try:
                first_runner = self._build_runner(first_store)
                opening = first_runner.run_dry_run_opening_tick(Decimal("100"))
                long_order_id = opening.lifecycle_update.created_order_ids[0]
                first_runner.apply_dry_run_order_execution(
                    order_id=long_order_id,
                    cumulative_filled_quantity=Decimal("4"),
                    status="PARTIALLY_FILLED",
                    mark_price=Decimal("100"),
                )
            finally:
                first_store.close()

            second_store = SqliteStateStore(state_path)
            try:
                restored_runner = self._build_runner(second_store)
                restored_runner.restore_state()
                lifecycle = restored_runner.dry_run_order_lifecycle
                self.assertIsNotNone(lifecycle)
                assert lifecycle is not None
                self.assertEqual(lifecycle.long_quantity, Decimal("4"))
                self.assertEqual(
                    lifecycle.get_order(long_order_id).cumulative_filled_quantity,
                    Decimal("4"),
                )

                risk = restored_runner.run_dry_run_hard_risk_tick(Decimal("99"))
                reduce_order_id = risk.lifecycle_update.created_order_ids[0]
                restored_runner.apply_dry_run_order_execution(
                    order_id=reduce_order_id,
                    cumulative_filled_quantity=Decimal("4"),
                    status="FILLED",
                    mark_price=Decimal("99"),
                )

                self.assertEqual(restored_runner.dry_run_order_lifecycle.long_quantity, Decimal("0"))
                self.assertEqual(restored_runner._state.sim_long_qty, Decimal("0"))
            finally:
                second_store.close()

    def test_run_cycle_advances_partial_orders_until_the_hedge_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = SqliteStateStore(Path(tmp_dir) / "state.db")
            try:
                runner = self._build_runner(
                    store,
                    dry_run_matching_engine=DryRunMatchingEngine(
                        fill_fraction=Decimal("0.5"),
                        min_fill_quantity=Decimal("1"),
                    ),
                )
                rows = [
                    {
                        "symbol": "BTCUSDT",
                        "close": Decimal("100"),
                        "liquidity": Decimal("0.9"),
                        "volatility": Decimal("0.8"),
                        "funding": Decimal("0.7"),
                        "margin": Decimal("0.85"),
                        "blocked": False,
                    }
                ]

                runner.run_cycle(rows=rows)
                self.assertEqual(runner.phase.value, "OPENING_HEDGE")
                self.assertEqual(runner.dry_run_order_lifecycle.long_quantity, Decimal("5"))

                for _ in range(4):
                    runner.run_cycle(rows=rows)

                self.assertEqual(runner.phase.value, "HEDGED")
                self.assertEqual(runner.dry_run_order_lifecycle.long_quantity, Decimal("10"))
                self.assertEqual(runner.dry_run_order_lifecycle.short_quantity, Decimal("10"))
            finally:
                store.close()

    def test_partial_orders_expire_and_requote_on_the_next_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = SqliteStateStore(Path(tmp_dir) / "state.db")
            try:
                runner = self._build_runner(
                    store,
                    dry_run_matching_engine=DryRunMatchingEngine(
                        fill_fraction=Decimal("0.5"),
                    ),
                    dry_run_order_timeout_cycles=1,
                    dry_run_max_requotes=2,
                )
                runner.run_cycle(rows=self._rows())
                runner.run_cycle(rows=self._rows())
                lifecycle = runner.dry_run_order_lifecycle

                self.assertEqual(lifecycle.expired_order_count, 2)
                self.assertEqual(lifecycle.timeout_requote_count, 1)
                self.assertEqual(
                    [order.order_id for order in lifecycle.active_orders],
                    ["dry-3", "dry-4"],
                )
                self.assertEqual(runner.phase.value, "OPENING_HEDGE")
            finally:
                store.close()

    def test_requote_limit_pauses_the_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = SqliteStateStore(Path(tmp_dir) / "state.db")
            try:
                runner = self._build_runner(
                    store,
                    dry_run_matching_engine=DryRunMatchingEngine(
                        fill_fraction=Decimal("0.5"),
                    ),
                    dry_run_order_timeout_cycles=1,
                    dry_run_max_requotes=1,
                )

                for _ in range(3):
                    runner.run_cycle(rows=self._rows())

                self.assertEqual(runner.phase.value, "PAUSED")
                self.assertEqual(
                    runner.dry_run_order_lifecycle.timeout_requote_count,
                    2,
                )
            finally:
                store.close()

    def _build_runner(
        self,
        state_store: SqliteStateStore,
        dry_run_matching_engine=None,
        dry_run_order_timeout_cycles: int = 0,
        dry_run_max_requotes: int = 0,
    ) -> LiveRunner:
        return LiveRunner(
            account_service=object(),
            stream_client=object(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=object(),
            transfer_planner=object(),
            dry_run_matching_engine=dry_run_matching_engine,
            dry_run_order_timeout_cycles=dry_run_order_timeout_cycles,
            dry_run_max_requotes=dry_run_max_requotes,
        )

    def _rows(self) -> list[dict]:
        return [
            {
                "symbol": "BTCUSDT",
                "close": Decimal("100"),
                "liquidity": Decimal("0.9"),
                "volatility": Decimal("0.8"),
                "funding": Decimal("0.7"),
                "margin": Decimal("0.85"),
                "blocked": False,
            }
        ]


if __name__ == "__main__":
    unittest.main()
