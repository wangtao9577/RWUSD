"""Rebalance decision helpers for post-take-profit hedge recovery."""

from dataclasses import dataclass
from decimal import Decimal
from src.domain.enums import StrategyPhase


ZERO = Decimal("0")


@dataclass(slots=True, frozen=True)
class RebalanceIntent:
    """Describes whether the strategy currently needs a rebalance action."""

    should_rebalance: bool
    long_adjustment: Decimal = ZERO
    short_adjustment: Decimal = ZERO


def plan_rebalance() -> RebalanceIntent:
    """Task 5 does not implement rebalance execution yet, only the boundary."""

    return RebalanceIntent(should_rebalance=False)


@dataclass(slots=True, frozen=True)
class RebalanceDecision:
    action: str
    reason: str | None = None


class RebalancePlanner:
    def decide(
        self,
        current_phase: StrategyPhase,
        risk_should_reduce: bool,
        risk_should_pause: bool,
        bull_mode: bool,
        allow_restore_now: bool = True,
    ) -> RebalanceDecision:
        if current_phase != StrategyPhase.REBALANCING:
            return RebalanceDecision(action="hold", reason="not_rebalancing")
        if risk_should_pause or risk_should_reduce:
            return RebalanceDecision(action="reduce_risk", reason="risk_block")
        if not allow_restore_now:
            return RebalanceDecision(
                action="restore_later",
                reason="await_next_eval_checkpoint",
            )
        if bull_mode:
            return RebalanceDecision(action="restore_later", reason="bull_mode_delay")
        return RebalanceDecision(action="restore_now", reason="safe_to_restore")
