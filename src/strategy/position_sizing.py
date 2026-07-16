from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal


@dataclass(slots=True, frozen=True)
class OrderSizingRule:
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal


def normalize_order_quantity(
    target_notional: Decimal,
    price: Decimal,
    rule: OrderSizingRule,
) -> Decimal | None:
    raw_quantity = target_notional / price
    normalized_quantity = raw_quantity.quantize(rule.step_size, rounding=ROUND_DOWN)
    if normalized_quantity < rule.min_qty:
        return None
    if normalized_quantity * price < rule.min_notional:
        return None
    return normalized_quantity
