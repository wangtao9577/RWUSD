from dataclasses import dataclass
from decimal import Decimal

from src.domain.models import PortfolioSnapshot


@dataclass(slots=True)
class RiskDecision:
    should_pause: bool
    should_reduce: bool
    reason: str | None
    should_redeem_topup: bool = False


class RiskRuleSet:
    def __init__(
        self,
        soft_unimmr: Decimal,
        hard_unimmr: Decimal,
        max_drawdown: Decimal,
        redeem_unimmr: Decimal | None = None,
        reserve_available_balance: Decimal | None = None,
        max_total_abs_leverage: Decimal | None = None,
        max_total_net_leverage: Decimal | None = None,
        max_single_symbol_net_leverage: Decimal | None = None,
    ) -> None:
        self._soft_unimmr = soft_unimmr
        self._hard_unimmr = hard_unimmr
        self._max_drawdown = max_drawdown
        self._redeem_unimmr = redeem_unimmr if redeem_unimmr is not None else Decimal("0")
        self._reserve_available_balance = (
            reserve_available_balance if reserve_available_balance is not None else Decimal("0")
        )
        self._max_total_abs_leverage = max_total_abs_leverage
        self._max_total_net_leverage = max_total_net_leverage
        self._max_single_symbol_net_leverage = max_single_symbol_net_leverage

    def _needs_redeem_topup(self, snapshot: PortfolioSnapshot) -> bool:
        if snapshot.uni_mmr < self._redeem_unimmr:
            return True
        return (
            self._reserve_available_balance > Decimal("0")
            and snapshot.available_balance < self._reserve_available_balance
        )

    def _exceeds_leverage_limit(
        self,
        notional: Decimal,
        max_leverage: Decimal | None,
        account_equity: Decimal,
    ) -> bool:
        if max_leverage is None:
            return False
        if account_equity <= Decimal("0"):
            return abs(notional) > Decimal("0")
        return abs(notional) / account_equity > max_leverage

    def evaluate(
        self,
        snapshot: PortfolioSnapshot,
        current_drawdown: Decimal,
    ) -> RiskDecision:
        should_redeem_topup = self._needs_redeem_topup(snapshot)
        if snapshot.uni_mmr < self._hard_unimmr:
            return RiskDecision(
                should_pause=True,
                should_reduce=True,
                should_redeem_topup=should_redeem_topup,
                reason="uni_mmr_hard_limit",
            )
        if self._exceeds_leverage_limit(
            snapshot.total_abs_notional,
            self._max_total_abs_leverage,
            snapshot.account_equity,
        ):
            return RiskDecision(
                should_pause=True,
                should_reduce=True,
                should_redeem_topup=should_redeem_topup,
                reason="total_abs_leverage_limit",
            )
        if self._exceeds_leverage_limit(
            snapshot.total_net_notional,
            self._max_total_net_leverage,
            snapshot.account_equity,
        ):
            return RiskDecision(
                should_pause=True,
                should_reduce=True,
                should_redeem_topup=should_redeem_topup,
                reason="total_net_leverage_limit",
            )
        if self._exceeds_leverage_limit(
            snapshot.single_symbol_net_notional,
            self._max_single_symbol_net_leverage,
            snapshot.account_equity,
        ):
            return RiskDecision(
                should_pause=True,
                should_reduce=True,
                should_redeem_topup=should_redeem_topup,
                reason="single_symbol_net_leverage_limit",
            )
        if current_drawdown > self._max_drawdown:
            return RiskDecision(
                should_pause=True,
                should_reduce=True,
                should_redeem_topup=False,
                reason="max_drawdown",
            )
        if snapshot.uni_mmr < self._soft_unimmr:
            return RiskDecision(
                should_pause=False,
                should_reduce=True,
                should_redeem_topup=should_redeem_topup,
                reason="uni_mmr_soft_limit",
            )
        if should_redeem_topup:
            return RiskDecision(
                should_pause=False,
                should_reduce=False,
                should_redeem_topup=True,
                reason="available_balance_reserve",
            )
        return RiskDecision(
            should_pause=False,
            should_reduce=False,
            should_redeem_topup=False,
            reason=None,
        )
