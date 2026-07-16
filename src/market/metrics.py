from decimal import Decimal


def weighted_symbol_score(
    liquidity: Decimal,
    volatility: Decimal,
    funding: Decimal,
    margin: Decimal,
) -> Decimal:
    return (
        liquidity * Decimal("0.35")
        + volatility * Decimal("0.20")
        + funding * Decimal("0.20")
        + margin * Decimal("0.25")
    )
