from decimal import Decimal

from src.domain.models import PortfolioSnapshot
from src.risk.rules import RiskDecision, RiskRuleSet


def evaluate_risk(
    snapshot: PortfolioSnapshot,
    current_drawdown: Decimal,
    rules: RiskRuleSet,
) -> RiskDecision:
    return rules.evaluate(
        snapshot=snapshot,
        current_drawdown=current_drawdown,
    )


__all__ = ["RiskDecision", "RiskRuleSet", "evaluate_risk"]
