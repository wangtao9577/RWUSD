"""In-memory order lifecycle used to rehearse hedge execution safely."""

from dataclasses import dataclass, replace
from decimal import Decimal

from src.domain.enums import PositionSide
from src.strategy.tick_quote_planner import QuoteOrder, TickQuotePlan, TickQuotePlanner


ZERO = Decimal("0")
ONE = Decimal("1")
ACTIVE_STATUSES = {"NEW", "PARTIALLY_FILLED"}
TERMINAL_STATUSES = {"CANCELED", "EXPIRED", "REJECTED", "FILLED"}


@dataclass(slots=True, frozen=True)
class DryRunOrder:
    order_id: str
    position_side: PositionSide
    side: str
    quantity: Decimal
    price: Decimal
    reduce_only: bool
    cumulative_filled_quantity: Decimal = ZERO
    status: str = "NEW"
    created_cycle: int = 0


@dataclass(slots=True, frozen=True)
class LifecycleUpdate:
    canceled_order_ids: tuple[str, ...] = ()
    created_order_ids: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class DryRunTickResult:
    plan: TickQuotePlan
    lifecycle_update: LifecycleUpdate


class DryRunMatchingEngine:
    """Deterministically fills marketable dry-run limit orders at a mark price."""

    def __init__(
        self,
        fill_fraction: Decimal = ONE,
        min_fill_quantity: Decimal = ZERO,
    ) -> None:
        if fill_fraction <= ZERO or fill_fraction > ONE:
            raise ValueError("fill_fraction_must_be_between_zero_and_one")
        if min_fill_quantity < ZERO:
            raise ValueError("min_fill_quantity_must_not_be_negative")
        self._fill_fraction = fill_fraction
        self._min_fill_quantity = min_fill_quantity

    def match(
        self,
        lifecycle: "DryRunOrderLifecycle",
        mark_price: Decimal,
    ) -> tuple[DryRunOrder, ...]:
        if mark_price <= ZERO:
            raise ValueError("mark_price_must_be_positive")
        matched: list[DryRunOrder] = []
        for order in lifecycle.active_orders:
            if not self._is_marketable(order, mark_price):
                continue
            remaining = order.quantity - order.cumulative_filled_quantity
            fill_delta = remaining * self._fill_fraction
            if self._min_fill_quantity > ZERO and fill_delta < self._min_fill_quantity:
                fill_delta = min(remaining, self._min_fill_quantity)
            cumulative_quantity = order.cumulative_filled_quantity + fill_delta
            status = "FILLED" if cumulative_quantity == order.quantity else "PARTIALLY_FILLED"
            matched.append(
                lifecycle.apply_execution(
                    order_id=order.order_id,
                    cumulative_filled_quantity=cumulative_quantity,
                    status=status,
                )
            )
        return tuple(matched)

    def _is_marketable(self, order: DryRunOrder, mark_price: Decimal) -> bool:
        return mark_price <= order.price if order.side == "BUY" else mark_price >= order.price


class DryRunOrderLifecycle:
    """Tracks replacement orders, cumulative fills, expiry, and risk reductions."""

    def __init__(
        self,
        long_quantity: Decimal = ZERO,
        short_quantity: Decimal = ZERO,
    ) -> None:
        if long_quantity < ZERO or short_quantity < ZERO:
            raise ValueError("initial_position_quantity_must_not_be_negative")
        self._long_quantity = long_quantity
        self._short_quantity = short_quantity
        self._orders: dict[str, DryRunOrder] = {}
        self._next_order_number = 1
        self._cycle = 0
        self._expired_order_count = 0
        self._timeout_requote_count = 0

    @property
    def long_quantity(self) -> Decimal:
        return self._long_quantity

    @property
    def short_quantity(self) -> Decimal:
        return self._short_quantity

    @property
    def active_orders(self) -> tuple[DryRunOrder, ...]:
        return tuple(
            order
            for order in self._orders.values()
            if order.status in ACTIVE_STATUSES
        )

    @property
    def cycle(self) -> int:
        return self._cycle

    @property
    def expired_order_count(self) -> int:
        return self._expired_order_count

    @property
    def timeout_requote_count(self) -> int:
        return self._timeout_requote_count

    def get_order(self, order_id: str) -> DryRunOrder:
        return self._orders[order_id]

    def to_snapshot(self) -> dict[str, object]:
        return {
            "long_quantity": str(self._long_quantity),
            "short_quantity": str(self._short_quantity),
            "next_order_number": self._next_order_number,
            "cycle": self._cycle,
            "expired_order_count": self._expired_order_count,
            "timeout_requote_count": self._timeout_requote_count,
            "orders": [
                {
                    "order_id": order.order_id,
                    "position_side": order.position_side.value,
                    "side": order.side,
                    "quantity": str(order.quantity),
                    "price": str(order.price),
                    "reduce_only": order.reduce_only,
                    "cumulative_filled_quantity": str(order.cumulative_filled_quantity),
                    "status": order.status,
                    "created_cycle": order.created_cycle,
                }
                for order in self._orders.values()
            ],
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, object]) -> "DryRunOrderLifecycle":
        lifecycle = cls(
            long_quantity=Decimal(str(snapshot.get("long_quantity", "0"))),
            short_quantity=Decimal(str(snapshot.get("short_quantity", "0"))),
        )
        lifecycle._next_order_number = max(1, int(snapshot.get("next_order_number", 1)))
        lifecycle._cycle = max(0, int(snapshot.get("cycle", 0)))
        lifecycle._expired_order_count = max(
            0,
            int(snapshot.get("expired_order_count", 0)),
        )
        lifecycle._timeout_requote_count = max(
            0,
            int(snapshot.get("timeout_requote_count", 0)),
        )
        raw_orders = snapshot.get("orders", [])
        if not isinstance(raw_orders, list):
            raise ValueError("orders_snapshot_must_be_a_list")
        for raw_order in raw_orders:
            if not isinstance(raw_order, dict):
                raise ValueError("order_snapshot_must_be_a_mapping")
            order = DryRunOrder(
                order_id=str(raw_order["order_id"]),
                position_side=PositionSide(str(raw_order["position_side"])),
                side=str(raw_order["side"]),
                quantity=Decimal(str(raw_order["quantity"])),
                price=Decimal(str(raw_order["price"])),
                reduce_only=bool(raw_order["reduce_only"]),
                cumulative_filled_quantity=Decimal(
                    str(raw_order.get("cumulative_filled_quantity", "0"))
                ),
                status=str(raw_order.get("status", "NEW")),
                created_cycle=max(0, int(raw_order.get("created_cycle", 0))),
            )
            lifecycle._orders[order.order_id] = order
        return lifecycle

    def advance_cycle(self) -> None:
        self._cycle += 1

    def expire_orders_older_than(self, max_age_cycles: int) -> tuple[DryRunOrder, ...]:
        if max_age_cycles <= 0:
            return ()
        expired: list[DryRunOrder] = []
        for order in self.active_orders:
            if self._cycle - order.created_cycle < max_age_cycles:
                continue
            expired.append(self.expire_order(order.order_id))
        self._expired_order_count += len(expired)
        return tuple(expired)

    def register_timeout_requote(self) -> int:
        self._timeout_requote_count += 1
        return self._timeout_requote_count

    def reset_timeout_requotes(self) -> None:
        self._timeout_requote_count = 0

    def replace_orders(self, plan: TickQuotePlan) -> LifecycleUpdate:
        canceled_order_ids: list[str] = []
        if plan.cancel_open_orders:
            for order in self.active_orders:
                self._orders[order.order_id] = replace(order, status="CANCELED")
                canceled_order_ids.append(order.order_id)

        created_order_ids: list[str] = []
        for quote in plan.orders:
            order = self._new_order(quote)
            self._orders[order.order_id] = order
            created_order_ids.append(order.order_id)
        return LifecycleUpdate(tuple(canceled_order_ids), tuple(created_order_ids))

    def apply_execution(
        self,
        *,
        order_id: str,
        cumulative_filled_quantity: Decimal,
        status: str,
    ) -> DryRunOrder:
        order = self.get_order(order_id)
        if order.status in {"CANCELED", "EXPIRED", "REJECTED"}:
            raise ValueError("order_not_active")
        if status not in {"PARTIALLY_FILLED", "FILLED"}:
            raise ValueError("unsupported_execution_status")
        if cumulative_filled_quantity < order.cumulative_filled_quantity:
            raise ValueError("cumulative_fill_must_not_decrease")
        if cumulative_filled_quantity > order.quantity:
            raise ValueError("cumulative_fill_exceeds_order_quantity")
        if status == "FILLED" and cumulative_filled_quantity != order.quantity:
            raise ValueError("filled_status_requires_full_quantity")

        fill_delta = cumulative_filled_quantity - order.cumulative_filled_quantity
        self._apply_position_delta(order, fill_delta)
        updated = replace(
            order,
            cumulative_filled_quantity=cumulative_filled_quantity,
            status=status,
        )
        self._orders[order_id] = updated
        return updated

    def expire_order(self, order_id: str) -> DryRunOrder:
        order = self.get_order(order_id)
        if order.status not in ACTIVE_STATUSES:
            raise ValueError("order_not_active")
        expired = replace(order, status="EXPIRED")
        self._orders[order_id] = expired
        return expired

    def replace_with_risk_reduction(self, mid_price: Decimal) -> LifecycleUpdate:
        return self.replace_orders(self.risk_reduction_plan(mid_price))

    def risk_reduction_plan(self, mid_price: Decimal) -> TickQuotePlan:
        if mid_price <= ZERO:
            raise ValueError("mid_price_must_be_positive")
        orders: list[QuoteOrder] = []
        if self._long_quantity > ZERO:
            orders.append(
                QuoteOrder(
                    position_side=PositionSide.LONG,
                    side="SELL",
                    quantity=self._long_quantity,
                    price=mid_price,
                    reduce_only=True,
                )
            )
        if self._short_quantity > ZERO:
            orders.append(
                QuoteOrder(
                    position_side=PositionSide.SHORT,
                    side="BUY",
                    quantity=self._short_quantity,
                    price=mid_price,
                    reduce_only=True,
                )
            )
        return TickQuotePlan(
            cancel_open_orders=True,
            orders=tuple(orders),
            reason="risk_reduce_both",
        )

    def _new_order(self, quote: QuoteOrder) -> DryRunOrder:
        if quote.quantity <= ZERO:
            raise ValueError("order_quantity_must_be_positive")
        if quote.price <= ZERO:
            raise ValueError("order_price_must_be_positive")
        order_id = f"dry-{self._next_order_number}"
        self._next_order_number += 1
        return DryRunOrder(
            order_id=order_id,
            position_side=quote.position_side,
            side=quote.side,
            quantity=quote.quantity,
            price=quote.price,
            reduce_only=quote.reduce_only,
            created_cycle=self._cycle,
        )

    def _apply_position_delta(self, order: DryRunOrder, fill_delta: Decimal) -> None:
        if fill_delta == ZERO:
            return
        if order.position_side == PositionSide.LONG:
            current_quantity = self._long_quantity
        else:
            current_quantity = self._short_quantity

        if order.reduce_only:
            if fill_delta > current_quantity:
                raise ValueError("reduce_only_fill_exceeds_position")
            next_quantity = current_quantity - fill_delta
        else:
            next_quantity = current_quantity + fill_delta

        if order.position_side == PositionSide.LONG:
            self._long_quantity = next_quantity
        else:
            self._short_quantity = next_quantity


class DryRunQuoteRuntime:
    """Combines quote planning and order lifecycle state for dry-run ticks."""

    def __init__(
        self,
        quote_planner: TickQuotePlanner,
        lifecycle: DryRunOrderLifecycle | None = None,
    ) -> None:
        self._quote_planner = quote_planner
        self.lifecycle = lifecycle or DryRunOrderLifecycle()

    def on_opening_tick(self, mid_price: Decimal) -> DryRunTickResult:
        plan = self._quote_planner.plan_opening(
            mid_price=mid_price,
            long_quantity=self.lifecycle.long_quantity,
            short_quantity=self.lifecycle.short_quantity,
        )
        return DryRunTickResult(plan, self.lifecycle.replace_orders(plan))

    def on_exit_tick(
        self,
        *,
        mid_price: Decimal,
        all_unrealized_pnl_realized: bool,
    ) -> DryRunTickResult:
        plan = self._quote_planner.plan_exit(
            mid_price=mid_price,
            long_quantity=self.lifecycle.long_quantity,
            short_quantity=self.lifecycle.short_quantity,
            all_unrealized_pnl_realized=all_unrealized_pnl_realized,
        )
        return DryRunTickResult(plan, self.lifecycle.replace_orders(plan))

    def on_hard_risk_tick(self, mid_price: Decimal) -> DryRunTickResult:
        plan = self.lifecycle.risk_reduction_plan(mid_price)
        update = self.lifecycle.replace_orders(plan)
        return DryRunTickResult(plan, update)
