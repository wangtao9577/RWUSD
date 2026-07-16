from dataclasses import dataclass
from decimal import Decimal

from src.domain.enums import PositionSide, StrategyPhase


ZERO = Decimal("0")


@dataclass(slots=True, frozen=True)
class StrategyIntent:
    action: str
    symbol: str | None = None
    long_notional: Decimal = ZERO
    short_notional: Decimal = ZERO
    side: PositionSide | None = None
    unrealized_pnl: Decimal = ZERO

    @classmethod
    def hold(cls) -> "StrategyIntent":
        return cls(action="hold")

    @classmethod
    def open_hedge(
        cls,
        symbol: str,
        long_notional: Decimal,
        short_notional: Decimal,
    ) -> "StrategyIntent":
        return cls(
            action="open_hedge",
            symbol=symbol,
            long_notional=long_notional,
            short_notional=short_notional,
        )

    @classmethod
    def take_profit(
        cls,
        symbol: str,
        side: PositionSide,
        unrealized_pnl: Decimal,
    ) -> "StrategyIntent":
        return cls(
            action="take_profit",
            symbol=symbol,
            side=side,
            unrealized_pnl=unrealized_pnl,
        )

    @classmethod
    def rebalance(
        cls,
        symbol: str,
        closed_side: PositionSide,
    ) -> "StrategyIntent":
        return cls(
            action="rebalance",
            symbol=symbol,
            side=closed_side,
        )


class HedgeEngine:
    def __init__(
        self,
        target_notional: Decimal,
        long_take_profit: Decimal,
        short_take_profit: Decimal,
    ) -> None:
        self._target_notional = target_notional
        self._long_take_profit = long_take_profit
        self._short_take_profit = short_take_profit
        self.phase = StrategyPhase.IDLE
        self.symbol: str | None = None

    def on_symbol_selected(self, symbol: str) -> StrategyIntent:
        if self.phase not in (StrategyPhase.IDLE, StrategyPhase.SELECTING_SYMBOL):
            return StrategyIntent.hold()

        self.symbol = symbol
        self.phase = StrategyPhase.OPENING_HEDGE
        return StrategyIntent.open_hedge(
            symbol=symbol,
            long_notional=self._target_notional,
            short_notional=self._target_notional,
        )

    def mark_hedged(self, symbol: str) -> None:
        self.symbol = symbol
        self.phase = StrategyPhase.HEDGED

    def on_pnl_update(
        self,
        long_unrealized: Decimal,
        short_unrealized: Decimal,
    ) -> StrategyIntent:
        if self.phase != StrategyPhase.HEDGED or self.symbol is None:
            return StrategyIntent.hold()

        if long_unrealized >= self._long_take_profit:
            self.phase = StrategyPhase.TAKING_PROFIT
            return StrategyIntent.take_profit(
                symbol=self.symbol,
                side=PositionSide.LONG,
                unrealized_pnl=long_unrealized,
            )

        if short_unrealized >= self._short_take_profit:
            self.phase = StrategyPhase.TAKING_PROFIT
            return StrategyIntent.take_profit(
                symbol=self.symbol,
                side=PositionSide.SHORT,
                unrealized_pnl=short_unrealized,
            )

        return StrategyIntent.hold()

    def on_take_profit_completed(
        self,
        symbol: str,
        closed_side: PositionSide,
    ) -> StrategyIntent:
        if self.symbol != symbol or self.phase != StrategyPhase.TAKING_PROFIT:
            return StrategyIntent.hold()

        self.phase = StrategyPhase.REBALANCING
        return StrategyIntent.rebalance(
            symbol=symbol,
            closed_side=closed_side,
        )

    def on_rebalance_restored(self, symbol: str) -> None:
        if self.symbol == symbol and self.phase == StrategyPhase.REBALANCING:
            self.phase = StrategyPhase.HEDGED
