"""Pure tick-level quote planning for a two-sided hedge."""

from dataclasses import dataclass
from decimal import Decimal

from src.domain.enums import PositionSide


ZERO = Decimal("0")


@dataclass(slots=True, frozen=True)
class QuoteOrder:
    """A limit-order intent. Execution remains the responsibility of an adapter."""

    position_side: PositionSide
    side: str
    quantity: Decimal
    price: Decimal
    reduce_only: bool


@dataclass(slots=True, frozen=True)
class TickQuotePlan:
    """The desired replacement orders after canceling stale tick orders."""

    cancel_open_orders: bool
    orders: tuple[QuoteOrder, ...]
    reason: str


class TickQuotePlanner:
    """Plans neutral hedge quotes from actual filled quantities, not fill counts."""

    def __init__(
        self,
        target_quantity: Decimal,
        balance_tolerance: Decimal = ZERO,
    ) -> None:
        if target_quantity <= ZERO:
            raise ValueError("target_quantity_must_be_positive")
        if balance_tolerance < ZERO:
            raise ValueError("balance_tolerance_must_not_be_negative")
        self._target_quantity = target_quantity
        self._balance_tolerance = balance_tolerance

    def plan_opening(
        self,
        *,
        mid_price: Decimal,
        long_quantity: Decimal,
        short_quantity: Decimal,
    ) -> TickQuotePlan:
        """Plan opening quotes while preserving a neutral long/short quantity."""

        self._validate_inputs(mid_price, long_quantity, short_quantity)
        if self._target_reached(long_quantity, short_quantity):
            return TickQuotePlan(True, (), "target_position_reached")

        if long_quantity > self._target_quantity or short_quantity > self._target_quantity:
            return self._plan_excess_and_deficit(
                mid_price=mid_price,
                long_quantity=long_quantity,
                short_quantity=short_quantity,
            )

        difference = long_quantity - short_quantity
        if abs(difference) <= self._balance_tolerance:
            quantity = self._target_quantity - max(long_quantity, short_quantity)
            return TickQuotePlan(
                True,
                (
                    self._open_order(PositionSide.LONG, quantity, mid_price),
                    self._open_order(PositionSide.SHORT, quantity, mid_price),
                ),
                "balanced_below_target_open_both",
            )

        missing_side = PositionSide.LONG if difference < ZERO else PositionSide.SHORT
        return TickQuotePlan(
            True,
            (self._open_order(missing_side, abs(difference), mid_price),),
            "unbalanced_open_missing_leg",
        )

    def plan_exit(
        self,
        *,
        mid_price: Decimal,
        long_quantity: Decimal,
        short_quantity: Decimal,
        all_unrealized_pnl_realized: bool,
    ) -> TickQuotePlan:
        """Plan the flowchart's realization step without adding directional exposure."""

        self._validate_inputs(mid_price, long_quantity, short_quantity)
        if all_unrealized_pnl_realized:
            return TickQuotePlan(True, (), "all_unrealized_pnl_realized")

        difference = long_quantity - short_quantity
        if abs(difference) <= self._balance_tolerance:
            quantity = min(long_quantity, short_quantity)
            if quantity <= ZERO:
                return TickQuotePlan(True, (), "no_position_to_reduce")
            return TickQuotePlan(
                True,
                (
                    self._reduce_order(PositionSide.LONG, quantity, mid_price),
                    self._reduce_order(PositionSide.SHORT, quantity, mid_price),
                ),
                "balanced_reduce_both",
            )

        missing_side = PositionSide.LONG if difference < ZERO else PositionSide.SHORT
        return TickQuotePlan(
            True,
            (self._open_order(missing_side, abs(difference), mid_price),),
            "unbalanced_restore_missing_leg",
        )

    def _target_reached(self, long_quantity: Decimal, short_quantity: Decimal) -> bool:
        return (
            long_quantity + self._balance_tolerance >= self._target_quantity
            and short_quantity + self._balance_tolerance >= self._target_quantity
        )

    def _plan_excess_and_deficit(
        self,
        *,
        mid_price: Decimal,
        long_quantity: Decimal,
        short_quantity: Decimal,
    ) -> TickQuotePlan:
        orders: list[QuoteOrder] = []
        if long_quantity > self._target_quantity:
            orders.append(
                self._reduce_order(
                    PositionSide.LONG,
                    long_quantity - self._target_quantity,
                    mid_price,
                )
            )
        elif long_quantity < self._target_quantity:
            orders.append(
                self._open_order(
                    PositionSide.LONG,
                    self._target_quantity - long_quantity,
                    mid_price,
                )
            )

        if short_quantity > self._target_quantity:
            orders.append(
                self._reduce_order(
                    PositionSide.SHORT,
                    short_quantity - self._target_quantity,
                    mid_price,
                )
            )
        elif short_quantity < self._target_quantity:
            orders.append(
                self._open_order(
                    PositionSide.SHORT,
                    self._target_quantity - short_quantity,
                    mid_price,
                )
            )

        return TickQuotePlan(True, tuple(orders), "trim_excess_and_fill_deficit")

    def _validate_inputs(
        self,
        mid_price: Decimal,
        long_quantity: Decimal,
        short_quantity: Decimal,
    ) -> None:
        if mid_price <= ZERO:
            raise ValueError("mid_price_must_be_positive")
        if long_quantity < ZERO or short_quantity < ZERO:
            raise ValueError("position_quantity_must_not_be_negative")

    def _open_order(
        self,
        position_side: PositionSide,
        quantity: Decimal,
        price: Decimal,
    ) -> QuoteOrder:
        side = "BUY" if position_side == PositionSide.LONG else "SELL"
        return QuoteOrder(position_side, side, quantity, price, False)

    def _reduce_order(
        self,
        position_side: PositionSide,
        quantity: Decimal,
        price: Decimal,
    ) -> QuoteOrder:
        side = "SELL" if position_side == PositionSide.LONG else "BUY"
        return QuoteOrder(position_side, side, quantity, price, True)
