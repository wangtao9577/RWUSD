from decimal import Decimal
import unittest

from src.strategy.leg_route import (
    ExecutionPreference,
    RestoreExecutionRoute,
    build_execution_route,
    build_execution_preference,
    build_restore_execution_route,
    resolve_restore_symbol,
)


class LegRouteTests(unittest.TestCase):
    def test_resolve_restore_symbol_prefers_usdc_when_available(self) -> None:
        self.assertEqual(
            resolve_restore_symbol(
                current_symbol="ETHUSDT",
                available_symbols={"ETHUSDT", "ETHUSDC"},
            ),
            "ETHUSDC",
        )

    def test_build_restore_execution_route_uses_usdc_maker_only_when_price_available(self) -> None:
        route = build_restore_execution_route(
            current_symbol="ETHUSDT",
            available_symbols={"ETHUSDT", "ETHUSDC"},
            reference_price=Decimal("1600"),
        )

        self.assertEqual(
            route,
            RestoreExecutionRoute(
                symbol="ETHUSDC",
                order_type="LIMIT",
                price=Decimal("1600"),
                time_in_force="GTX",
                reduce_only=False,
                maker_only=True,
            ),
        )

    def test_build_restore_execution_route_falls_back_to_market_when_price_missing(self) -> None:
        route = build_restore_execution_route(
            current_symbol="ETHUSDT",
            available_symbols={"ETHUSDT", "ETHUSDC"},
            reference_price=None,
        )

        self.assertEqual(
            route,
            RestoreExecutionRoute(
                symbol="ETHUSDC",
                order_type="MARKET",
                price=None,
                time_in_force=None,
                reduce_only=None,
                maker_only=False,
            ),
        )

    def test_build_restore_execution_route_stays_on_current_symbol_when_usdc_unavailable(self) -> None:
        route = build_restore_execution_route(
            current_symbol="BTCUSDT",
            available_symbols={"BTCUSDT"},
            reference_price=Decimal("60000"),
        )

        self.assertEqual(
            route,
            RestoreExecutionRoute(
                symbol="BTCUSDT",
                order_type="MARKET",
                price=None,
                time_in_force=None,
                reduce_only=None,
                maker_only=False,
            ),
        )

    def test_build_execution_preference_prefers_zero_fee_usdc_route_when_available(self) -> None:
        preference = build_execution_preference(
            anchor_symbol="ETHUSDT",
            available_symbols={"ETHUSDT", "ETHUSDC"},
        )

        self.assertEqual(
            preference,
            ExecutionPreference(
                anchor_symbol="ETHUSDT",
                preferred_symbol="ETHUSDC",
                execution_cost_bps=Decimal("0"),
                maker_only=True,
            ),
        )

    def test_build_execution_preference_falls_back_to_default_taker_cost(self) -> None:
        preference = build_execution_preference(
            anchor_symbol="BTCUSDT",
            available_symbols={"BTCUSDT"},
        )

        self.assertEqual(
            preference,
            ExecutionPreference(
                anchor_symbol="BTCUSDT",
                preferred_symbol="BTCUSDT",
                execution_cost_bps=Decimal("5"),
                maker_only=False,
            ),
        )

    def test_build_execution_route_uses_usdc_maker_for_recover_missing_leg_when_enabled(self) -> None:
        route = build_execution_route(
            execution_stage="recover_missing_leg",
            anchor_symbol="ETHUSDT",
            available_symbols={"ETHUSDT", "ETHUSDC"},
            reference_price=Decimal("1600"),
            maker_enabled=True,
            maker_allowed_phases={"open_hedge", "restore_now", "recover_missing_leg"},
            fallback_to_market_on_missing_price=True,
        )

        self.assertEqual(route.anchor_symbol, "ETHUSDT")
        self.assertEqual(route.execution_stage, "recover_missing_leg")
        self.assertEqual(route.symbol, "ETHUSDC")
        self.assertEqual(route.order_type, "LIMIT")
        self.assertEqual(route.price, Decimal("1600"))
        self.assertEqual(route.time_in_force, "GTX")
        self.assertEqual(route.reduce_only, False)
        self.assertTrue(route.maker_only)
        self.assertIsNone(route.fallback_reason)

    def test_build_execution_route_falls_back_to_anchor_market_when_maker_disabled(self) -> None:
        route = build_execution_route(
            execution_stage="open_hedge",
            anchor_symbol="ETHUSDT",
            available_symbols={"ETHUSDT", "ETHUSDC"},
            reference_price=Decimal("1600"),
            maker_enabled=False,
            maker_allowed_phases={"open_hedge", "restore_now", "recover_missing_leg"},
            fallback_to_market_on_missing_price=True,
        )

        self.assertEqual(route.anchor_symbol, "ETHUSDT")
        self.assertEqual(route.execution_stage, "open_hedge")
        self.assertEqual(route.symbol, "ETHUSDT")
        self.assertEqual(route.order_type, "MARKET")
        self.assertIsNone(route.price)
        self.assertIsNone(route.time_in_force)
        self.assertIsNone(route.reduce_only)
        self.assertFalse(route.maker_only)
        self.assertEqual(route.fallback_reason, "maker_disabled")


if __name__ == "__main__":
    unittest.main()
