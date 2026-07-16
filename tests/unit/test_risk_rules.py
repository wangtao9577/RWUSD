from decimal import Decimal
import unittest

from src.domain.models import PortfolioSnapshot
from src.risk.guards import evaluate_risk
from src.risk.rules import RiskDecision, RiskRuleSet


class RiskRuleSetTests(unittest.TestCase):
    def test_guard_evaluate_risk_delegates_to_rules(self) -> None:
        rules = RiskRuleSet(
            soft_unimmr=Decimal("12"),
            hard_unimmr=Decimal("8"),
            max_drawdown=Decimal("0.15"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            uni_mmr=Decimal("7.5"),
        )

        decision = evaluate_risk(
            snapshot=snapshot,
            current_drawdown=Decimal("0.05"),
            rules=rules,
        )

        self.assertEqual(
            decision,
            RiskDecision(
                should_pause=True,
                should_reduce=True,
                reason="uni_mmr_hard_limit",
            ),
        )

    def test_hard_uni_mmr_breach_pauses_and_reduces(self) -> None:
        rules = RiskRuleSet(
            soft_unimmr=Decimal("12"),
            hard_unimmr=Decimal("8"),
            max_drawdown=Decimal("0.15"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            uni_mmr=Decimal("7.5"),
        )

        decision = rules.evaluate(snapshot, current_drawdown=Decimal("0.05"))

        self.assertEqual(
            decision,
            RiskDecision(
                should_pause=True,
                should_reduce=True,
                reason="uni_mmr_hard_limit",
            ),
        )

    def test_soft_uni_mmr_breach_only_reduces(self) -> None:
        rules = RiskRuleSet(
            soft_unimmr=Decimal("12"),
            hard_unimmr=Decimal("8"),
            max_drawdown=Decimal("0.15"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            uni_mmr=Decimal("9.5"),
        )

        decision = rules.evaluate(snapshot, current_drawdown=Decimal("0.05"))

        self.assertEqual(decision.should_pause, False)
        self.assertEqual(decision.should_reduce, True)
        self.assertEqual(decision.reason, "uni_mmr_soft_limit")

    def test_drawdown_breach_pauses_and_reduces(self) -> None:
        rules = RiskRuleSet(
            soft_unimmr=Decimal("12"),
            hard_unimmr=Decimal("8"),
            max_drawdown=Decimal("0.15"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            uni_mmr=Decimal("15"),
        )

        decision = rules.evaluate(snapshot, current_drawdown=Decimal("0.20"))

        self.assertEqual(decision.should_pause, True)
        self.assertEqual(decision.should_reduce, True)
        self.assertEqual(decision.reason, "max_drawdown")

    def test_safe_snapshot_does_not_trigger_risk_actions(self) -> None:
        rules = RiskRuleSet(
            soft_unimmr=Decimal("12"),
            hard_unimmr=Decimal("8"),
            max_drawdown=Decimal("0.15"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            uni_mmr=Decimal("14"),
        )

        decision = rules.evaluate(snapshot, current_drawdown=Decimal("0.10"))

        self.assertEqual(
            decision,
            RiskDecision(
                should_pause=False,
                should_reduce=False,
                reason=None,
            ),
        )

    def test_hard_uni_mmr_threshold_is_not_a_breach_when_equal(self) -> None:
        rules = RiskRuleSet(
            soft_unimmr=Decimal("12"),
            hard_unimmr=Decimal("8"),
            max_drawdown=Decimal("0.15"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            uni_mmr=Decimal("8"),
        )

        decision = rules.evaluate(snapshot, current_drawdown=Decimal("0.10"))

        self.assertEqual(decision.should_pause, False)
        self.assertEqual(decision.should_reduce, True)
        self.assertEqual(decision.reason, "uni_mmr_soft_limit")

    def test_soft_uni_mmr_threshold_is_not_a_breach_when_equal(self) -> None:
        rules = RiskRuleSet(
            soft_unimmr=Decimal("12"),
            hard_unimmr=Decimal("8"),
            max_drawdown=Decimal("0.15"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            uni_mmr=Decimal("12"),
        )

        decision = rules.evaluate(snapshot, current_drawdown=Decimal("0.10"))

        self.assertEqual(
            decision,
            RiskDecision(
                should_pause=False,
                should_reduce=False,
                reason=None,
            ),
        )

    def test_drawdown_threshold_is_not_a_breach_when_equal(self) -> None:
        rules = RiskRuleSet(
            soft_unimmr=Decimal("12"),
            hard_unimmr=Decimal("8"),
            max_drawdown=Decimal("0.15"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            uni_mmr=Decimal("15"),
        )

        decision = rules.evaluate(snapshot, current_drawdown=Decimal("0.15"))

        self.assertEqual(
            decision,
            RiskDecision(
                should_pause=False,
                should_reduce=False,
                reason=None,
            ),
        )

    def test_custom_thresholds_can_make_same_snapshot_safe(self) -> None:
        strict_rules = RiskRuleSet(
            soft_unimmr=Decimal("12"),
            hard_unimmr=Decimal("8"),
            max_drawdown=Decimal("0.15"),
        )
        relaxed_rules = RiskRuleSet(
            soft_unimmr=Decimal("7"),
            hard_unimmr=Decimal("5"),
            max_drawdown=Decimal("0.20"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            uni_mmr=Decimal("9.5"),
        )

        strict_decision = strict_rules.evaluate(snapshot, current_drawdown=Decimal("0.10"))
        relaxed_decision = relaxed_rules.evaluate(snapshot, current_drawdown=Decimal("0.10"))

        self.assertTrue(strict_decision.should_reduce)
        self.assertFalse(relaxed_decision.should_reduce)

    def test_soft_unimmr_breach_requests_redeem_topup_without_full_pause(self) -> None:
        rules = RiskRuleSet(
            soft_unimmr=Decimal("12"),
            hard_unimmr=Decimal("8"),
            max_drawdown=Decimal("0.15"),
            redeem_unimmr=Decimal("10"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            uni_mmr=Decimal("9.5"),
        )

        decision = rules.evaluate(snapshot, current_drawdown=Decimal("0.05"))

        self.assertTrue(decision.should_reduce)
        self.assertTrue(decision.should_redeem_topup)
        self.assertFalse(decision.should_pause)

    def test_safe_snapshot_does_not_request_redeem_topup(self) -> None:
        rules = RiskRuleSet(
            soft_unimmr=Decimal("12"),
            hard_unimmr=Decimal("8"),
            max_drawdown=Decimal("0.15"),
            redeem_unimmr=Decimal("10"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            uni_mmr=Decimal("14"),
        )

        decision = rules.evaluate(snapshot, current_drawdown=Decimal("0.10"))

        self.assertFalse(decision.should_redeem_topup)

    def test_low_available_balance_requests_redeem_topup_even_when_unimmr_is_safe(self) -> None:
        rules = RiskRuleSet(
            soft_unimmr=Decimal("12"),
            hard_unimmr=Decimal("8"),
            max_drawdown=Decimal("0.15"),
            redeem_unimmr=Decimal("10"),
            reserve_available_balance=Decimal("300"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            available_balance=Decimal("260"),
            uni_mmr=Decimal("14"),
        )

        decision = rules.evaluate(snapshot, current_drawdown=Decimal("0.10"))

        self.assertFalse(decision.should_pause)
        self.assertFalse(decision.should_reduce)
        self.assertTrue(decision.should_redeem_topup)
        self.assertEqual(decision.reason, "available_balance_reserve")

    def test_total_absolute_leverage_limit_pauses_and_reduces(self) -> None:
        rules = RiskRuleSet(
            soft_unimmr=Decimal("6"),
            hard_unimmr=Decimal("4"),
            max_drawdown=Decimal("0.15"),
            max_total_abs_leverage=Decimal("10"),
        )

        decision = rules.evaluate(
            PortfolioSnapshot(
                account_equity=Decimal("1000"),
                uni_mmr=Decimal("12"),
                total_abs_notional=Decimal("10001"),
            ),
            current_drawdown=Decimal("0"),
        )

        self.assertTrue(decision.should_pause)
        self.assertTrue(decision.should_reduce)
        self.assertEqual(decision.reason, "total_abs_leverage_limit")

    def test_net_and_single_symbol_limits_are_evaluated_independently(self) -> None:
        rules = RiskRuleSet(
            soft_unimmr=Decimal("6"),
            hard_unimmr=Decimal("4"),
            max_drawdown=Decimal("0.15"),
            max_total_net_leverage=Decimal("1"),
            max_single_symbol_net_leverage=Decimal("0.5"),
        )
        net_breach = rules.evaluate(
            PortfolioSnapshot(
                account_equity=Decimal("1000"),
                uni_mmr=Decimal("12"),
                total_net_notional=Decimal("1001"),
                single_symbol_net_notional=Decimal("100"),
            ),
            current_drawdown=Decimal("0"),
        )
        single_symbol_breach = rules.evaluate(
            PortfolioSnapshot(
                account_equity=Decimal("1000"),
                uni_mmr=Decimal("12"),
                total_net_notional=Decimal("100"),
                single_symbol_net_notional=Decimal("501"),
            ),
            current_drawdown=Decimal("0"),
        )

        self.assertEqual(net_breach.reason, "total_net_leverage_limit")
        self.assertEqual(single_symbol_breach.reason, "single_symbol_net_leverage_limit")


if __name__ == "__main__":
    unittest.main()
