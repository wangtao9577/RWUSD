import unittest

from src.domain.enums import StrategyPhase
from src.strategy.rebalance import RebalancePlanner


class RebalancePlannerTests(unittest.TestCase):
    def test_rebalance_returns_hold_when_not_in_rebalancing_phase(self) -> None:
        planner = RebalancePlanner()

        decision = planner.decide(
            current_phase=StrategyPhase.IDLE,
            risk_should_reduce=False,
            risk_should_pause=False,
            bull_mode=False,
        )

        self.assertEqual(decision.action, "hold")
        self.assertEqual(decision.reason, "not_rebalancing")

    def test_rebalance_returns_restore_now_when_risk_is_safe(self) -> None:
        planner = RebalancePlanner()

        decision = planner.decide(
            current_phase=StrategyPhase.REBALANCING,
            risk_should_reduce=False,
            risk_should_pause=False,
            bull_mode=False,
        )

        self.assertEqual(decision.action, "restore_now")
        self.assertEqual(decision.reason, "safe_to_restore")

    def test_rebalance_returns_restore_later_when_bull_mode_is_on(self) -> None:
        planner = RebalancePlanner()

        decision = planner.decide(
            current_phase=StrategyPhase.REBALANCING,
            risk_should_reduce=False,
            risk_should_pause=False,
            bull_mode=True,
        )

        self.assertEqual(decision.action, "restore_later")
        self.assertEqual(decision.reason, "bull_mode_delay")

    def test_rebalance_returns_reduce_risk_when_risk_manager_blocks_restore(self) -> None:
        planner = RebalancePlanner()

        decision = planner.decide(
            current_phase=StrategyPhase.REBALANCING,
            risk_should_reduce=True,
            risk_should_pause=False,
            bull_mode=False,
        )

        self.assertEqual(decision.action, "reduce_risk")
        self.assertEqual(decision.reason, "risk_block")

    def test_rebalance_returns_reduce_risk_when_risk_manager_pauses_restore(self) -> None:
        planner = RebalancePlanner()

        decision = planner.decide(
            current_phase=StrategyPhase.REBALANCING,
            risk_should_reduce=False,
            risk_should_pause=True,
            bull_mode=False,
        )

        self.assertEqual(decision.action, "reduce_risk")
        self.assertEqual(decision.reason, "risk_block")

    def test_rebalance_returns_restore_later_when_restore_checkpoint_not_reached(self) -> None:
        planner = RebalancePlanner()

        decision = planner.decide(
            current_phase=StrategyPhase.REBALANCING,
            risk_should_reduce=False,
            risk_should_pause=False,
            bull_mode=False,
            allow_restore_now=False,
        )

        self.assertEqual(decision.action, "restore_later")
        self.assertEqual(decision.reason, "await_next_eval_checkpoint")


if __name__ == "__main__":
    unittest.main()
