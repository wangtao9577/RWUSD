from decimal import Decimal
import typing
import unittest
from unittest.mock import patch

from src.market.metrics import weighted_symbol_score
import src.market.selector as selector_module
from src.market.selector import SelectorRow, SymbolSelector


class SymbolSelectorTests(unittest.TestCase):
    def test_resolve_not_required_falls_back_to_typing_extensions(self) -> None:
        sentinel = object()

        with patch.object(selector_module.typing, "NotRequired", None, create=True):
            with patch.object(
                selector_module,
                "_typing_extensions_not_required",
                sentinel,
                create=True,
            ):
                self.assertIs(selector_module._resolve_not_required(), sentinel)

    def test_select_uses_selector_row_input_contract(self) -> None:
        hints = typing.get_type_hints(SymbolSelector.select)
        rows_type = hints["rows"]

        self.assertEqual(str(rows_type), "list[src.market.selector.SelectorRow]")

    def test_weighted_symbol_score_uses_task_3_weights(self) -> None:
        score = weighted_symbol_score(
            liquidity=Decimal("1.00"),
            volatility=Decimal("2.00"),
            funding=Decimal("3.00"),
            margin=Decimal("4.00"),
        )

        self.assertEqual(score, Decimal("2.35"))

    def test_select_picks_highest_scoring_non_blocked_symbol(self) -> None:
        selector = SymbolSelector(switch_threshold=Decimal("0.20"))
        rows = [
            {
                "symbol": "ETHUSDT",
                "liquidity": Decimal("0.80"),
                "volatility": Decimal("0.70"),
                "funding": Decimal("0.50"),
                "margin": Decimal("0.60"),
                "blocked": False,
            },
            {
                "symbol": "BTCUSDT",
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.80"),
                "funding": Decimal("0.60"),
                "margin": Decimal("0.90"),
                "blocked": False,
            },
            {
                "symbol": "BNBUSDT",
                "liquidity": Decimal("0.99"),
                "volatility": Decimal("0.99"),
                "funding": Decimal("0.99"),
                "margin": Decimal("0.99"),
                "blocked": True,
            },
        ]

        snapshot = selector.select(current_symbol=None, rows=rows)

        self.assertEqual(snapshot.selected_symbol, "BTCUSDT")
        self.assertEqual(snapshot.scores[0].symbol, "BTCUSDT")
        self.assertEqual(snapshot.scores[-1].reject_reason, "blocked")

    def test_select_keeps_current_symbol_when_gap_is_below_switch_threshold(self) -> None:
        selector = SymbolSelector(switch_threshold=Decimal("0.10"))
        rows = [
            {
                "symbol": "BTCUSDT",
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.85"),
                "funding": Decimal("0.90"),
                "margin": Decimal("0.80"),
                "blocked": False,
            },
            {
                "symbol": "ETHUSDT",
                "liquidity": Decimal("0.88"),
                "volatility": Decimal("0.84"),
                "funding": Decimal("0.88"),
                "margin": Decimal("0.79"),
                "blocked": False,
            },
        ]

        snapshot = selector.select(current_symbol="ETHUSDT", rows=rows)

        self.assertEqual(snapshot.selected_symbol, "ETHUSDT")
        self.assertEqual(snapshot.scores[0].symbol, "BTCUSDT")

    def test_select_keeps_current_symbol_between_eval_checkpoints(self) -> None:
        selector = SymbolSelector(
            switch_threshold=Decimal("0.10"),
            eval_interval_minutes=15,
        )
        rows = [
            {
                "symbol": "BTCUSDT",
                "liquidity": Decimal("0.60"),
                "volatility": Decimal("0.60"),
                "funding": Decimal("0.60"),
                "margin": Decimal("0.60"),
                "blocked": False,
            },
            {
                "symbol": "ETHUSDT",
                "liquidity": Decimal("1.00"),
                "volatility": Decimal("1.00"),
                "funding": Decimal("1.00"),
                "margin": Decimal("1.00"),
                "blocked": False,
            },
        ]

        snapshot = selector.select(
            current_symbol="BTCUSDT",
            rows=rows,
            minute_of_day=7,
        )

        self.assertEqual(snapshot.selected_symbol, "BTCUSDT")
        self.assertEqual(snapshot.selected_symbols, ["BTCUSDT"])
        self.assertEqual(snapshot.cooldown_symbol, "BTCUSDT")
        self.assertEqual(snapshot.scores[0].symbol, "ETHUSDT")

    def test_select_keeps_current_symbol_during_switch_cooldown_window(self) -> None:
        selector = SymbolSelector(
            switch_threshold=Decimal("0.10"),
            eval_interval_minutes=15,
            switch_cooldown_minutes=30,
        )
        rows = [
            {
                "symbol": "BTCUSDT",
                "liquidity": Decimal("0.60"),
                "volatility": Decimal("0.60"),
                "funding": Decimal("0.60"),
                "margin": Decimal("0.60"),
                "blocked": False,
            },
            {
                "symbol": "ETHUSDT",
                "liquidity": Decimal("1.00"),
                "volatility": Decimal("1.00"),
                "funding": Decimal("1.00"),
                "margin": Decimal("1.00"),
                "blocked": False,
            },
        ]

        snapshot = selector.select(
            current_symbol="BTCUSDT",
            rows=rows,
            minute_of_day=15,
            last_switch_minute=0,
        )

        self.assertEqual(snapshot.selected_symbol, "BTCUSDT")
        self.assertEqual(snapshot.selected_symbols, ["BTCUSDT"])
        self.assertEqual(snapshot.cooldown_symbol, "BTCUSDT")
        self.assertEqual(snapshot.scores[0].symbol, "ETHUSDT")

    def test_select_allows_switch_after_switch_cooldown_window_expires(self) -> None:
        selector = SymbolSelector(
            switch_threshold=Decimal("0.10"),
            eval_interval_minutes=15,
            switch_cooldown_minutes=30,
        )
        rows = [
            {
                "symbol": "BTCUSDT",
                "liquidity": Decimal("0.60"),
                "volatility": Decimal("0.60"),
                "funding": Decimal("0.60"),
                "margin": Decimal("0.60"),
                "blocked": False,
            },
            {
                "symbol": "ETHUSDT",
                "liquidity": Decimal("1.00"),
                "volatility": Decimal("1.00"),
                "funding": Decimal("1.00"),
                "margin": Decimal("1.00"),
                "blocked": False,
            },
        ]

        snapshot = selector.select(
            current_symbol="BTCUSDT",
            rows=rows,
            minute_of_day=30,
            last_switch_minute=0,
        )

        self.assertEqual(snapshot.selected_symbol, "ETHUSDT")
        self.assertEqual(snapshot.selected_symbols, ["ETHUSDT"])

    def test_select_falls_back_to_best_symbol_when_current_symbol_is_blocked(self) -> None:
        selector = SymbolSelector(switch_threshold=Decimal("0.10"))
        rows = [
            {
                "symbol": "BTCUSDT",
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.85"),
                "funding": Decimal("0.80"),
                "margin": Decimal("0.88"),
                "blocked": False,
            },
            {
                "symbol": "ETHUSDT",
                "liquidity": Decimal("0.95"),
                "volatility": Decimal("0.90"),
                "funding": Decimal("0.90"),
                "margin": Decimal("0.92"),
                "blocked": True,
            },
        ]

        snapshot = selector.select(current_symbol="ETHUSDT", rows=rows)

        self.assertEqual(snapshot.selected_symbol, "BTCUSDT")

    def test_select_returns_none_when_all_candidates_are_rejected(self) -> None:
        selector = SymbolSelector(switch_threshold=Decimal("0.10"))
        rows = [
            {
                "symbol": "BTCUSDT",
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.85"),
                "funding": Decimal("0.80"),
                "margin": Decimal("0.88"),
                "blocked": True,
            },
            {
                "symbol": "ETHUSDT",
                "liquidity": Decimal("0.88"),
                "volatility": Decimal("0.84"),
                "funding": Decimal("0.78"),
                "margin": Decimal("0.86"),
                "blocked": True,
            },
        ]

        snapshot = selector.select(current_symbol="ETHUSDT", rows=rows)

        self.assertIsNone(snapshot.selected_symbol)

    def test_select_switches_when_gap_equals_switch_threshold(self) -> None:
        selector = SymbolSelector(switch_threshold=Decimal("0.10"))
        rows = [
            {
                "symbol": "BTCUSDT",
                "liquidity": Decimal("1.00"),
                "volatility": Decimal("0.00"),
                "funding": Decimal("0.00"),
                "margin": Decimal("0.00"),
                "blocked": False,
            },
            {
                "symbol": "ETHUSDT",
                "liquidity": Decimal("0.7142857142857142857142857143"),
                "volatility": Decimal("0.00"),
                "funding": Decimal("0.00"),
                "margin": Decimal("0.00"),
                "blocked": False,
            },
        ]

        snapshot = selector.select(current_symbol="ETHUSDT", rows=rows)

        self.assertEqual(snapshot.selected_symbol, "BTCUSDT")

    def test_select_many_returns_top_n_non_blocked_symbols(self) -> None:
        selector = SymbolSelector(switch_threshold=Decimal("0.10"))
        rows = [
            {
                "symbol": "BTCUSDT",
                "liquidity": Decimal("0.95"),
                "volatility": Decimal("0.85"),
                "funding": Decimal("0.70"),
                "margin": Decimal("0.92"),
                "blocked": False,
            },
            {
                "symbol": "ETHUSDT",
                "liquidity": Decimal("0.94"),
                "volatility": Decimal("0.84"),
                "funding": Decimal("0.69"),
                "margin": Decimal("0.91"),
                "blocked": False,
            },
            {
                "symbol": "SOLUSDT",
                "liquidity": Decimal("0.93"),
                "volatility": Decimal("0.83"),
                "funding": Decimal("0.68"),
                "margin": Decimal("0.90"),
                "blocked": False,
            },
            {
                "symbol": "BNBUSDT",
                "liquidity": Decimal("0.99"),
                "volatility": Decimal("0.99"),
                "funding": Decimal("0.99"),
                "margin": Decimal("0.99"),
                "blocked": True,
            },
        ]

        snapshot = selector.select_many(rows=rows, limit=2)

        self.assertEqual(snapshot.selected_symbols, ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(snapshot.selected_symbol, "BTCUSDT")
        self.assertEqual(snapshot.scores[-1].reject_reason, "blocked")

    def test_select_prefers_lower_execution_cost_when_core_scores_are_close(self) -> None:
        selector = SymbolSelector(switch_threshold=Decimal("0.10"))
        rows = [
            {
                "symbol": "BTCUSDT",
                "liquidity": Decimal("0.90"),
                "volatility": Decimal("0.85"),
                "funding": Decimal("0.80"),
                "margin": Decimal("0.88"),
                "blocked": False,
                "execution_cost_bps": Decimal("5"),
                "preferred_execution_symbol": "BTCUSDT",
            },
            {
                "symbol": "ETHUSDT",
                "liquidity": Decimal("0.89"),
                "volatility": Decimal("0.85"),
                "funding": Decimal("0.80"),
                "margin": Decimal("0.88"),
                "blocked": False,
                "execution_cost_bps": Decimal("0"),
                "preferred_execution_symbol": "ETHUSDC",
            },
        ]

        snapshot = selector.select(current_symbol=None, rows=rows)

        self.assertEqual(snapshot.selected_symbol, "ETHUSDT")
        self.assertEqual(snapshot.scores[0].preferred_execution_symbol, "ETHUSDC")
        self.assertEqual(snapshot.scores[0].execution_cost_bps, Decimal("0"))

    def test_select_exposes_execution_cost_fields_in_scores(self) -> None:
        selector = SymbolSelector(switch_threshold=Decimal("0.10"))
        rows = [
            {
                "symbol": "ETHUSDT",
                "liquidity": Decimal("0.80"),
                "volatility": Decimal("0.70"),
                "funding": Decimal("0.50"),
                "margin": Decimal("0.60"),
                "blocked": False,
                "execution_cost_bps": Decimal("0"),
                "preferred_execution_symbol": "ETHUSDC",
            },
        ]

        snapshot = selector.select(current_symbol=None, rows=rows)

        self.assertEqual(snapshot.scores[0].preferred_execution_symbol, "ETHUSDC")
        self.assertEqual(snapshot.scores[0].execution_cost_bps, Decimal("0"))
        self.assertGreater(snapshot.scores[0].execution_cost_score, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
