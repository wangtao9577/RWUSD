import json
from decimal import Decimal, InvalidOperation
from pathlib import Path


DEFAULT_INITIAL_CAPITAL_USDT = Decimal("10000")
ZERO = Decimal("0")


def build_simulation_outcome(
    *,
    summary: dict[str, object],
    snapshot: dict[str, object],
    initial_capital_usdt: Decimal | str = DEFAULT_INITIAL_CAPITAL_USDT,
) -> dict[str, object]:
    principal = _to_decimal(summary.get("rwusd_principal", "0"))
    interest = _to_decimal(summary.get("rwusd_interest_accrued", "0"))
    harvest_buffer = _to_decimal(
        _mapping(snapshot.get("strategy")).get("harvest_buffer", "0")
    )
    profit_sweep_count = int(summary.get("profit_sweep_count", 0) or 0)
    redeem_topup_count = int(summary.get("redeem_topup_count", 0) or 0)
    risk_reason_counts = _mapping(summary.get("risk_reason_counts"))
    hard_limit_breach_count = int(risk_reason_counts.get("uni_mmr_hard_limit", 0) or 0)
    soft_limit_breach_count = int(risk_reason_counts.get("uni_mmr_soft_limit", 0) or 0)
    current_uni_mmr = _to_decimal(
        _mapping(snapshot.get("account")).get("uni_mmr", "0")
    )
    initial_capital = _to_decimal(initial_capital_usdt)

    if hard_limit_breach_count > 0:
        health = "hard_limit_seen"
    elif redeem_topup_count > 0:
        health = "soft_limit_seen"
    else:
        health = "healthy"

    if principal <= ZERO or hard_limit_breach_count > 0:
        verdict = "fail"
    elif profit_sweep_count > 0 and redeem_topup_count == 0:
        verdict = "pass"
    else:
        verdict = "borderline"

    return {
        "initial_capital_usdt": _decimal_to_str(initial_capital),
        "rwusd_principal": _decimal_to_str(principal),
        "rwusd_interest_accrued": _decimal_to_str(interest),
        "harvest_buffer": _decimal_to_str(harvest_buffer),
        "profit_sweep_count": profit_sweep_count,
        "redeem_topup_count": redeem_topup_count,
        "rebalance_action_counts": _mapping(summary.get("rebalance_action_counts")),
        "selected_symbol_counts": _mapping(summary.get("selected_symbol_counts")),
        "risk_reason_counts": risk_reason_counts,
        "uni_mmr": {
            "current": _decimal_to_str(current_uni_mmr),
            "soft_limit_breach_count": soft_limit_breach_count,
            "hard_limit_breach_count": hard_limit_breach_count,
            "health": health,
        },
        "verdict": verdict,
    }


def write_simulation_outcome(
    *,
    summary: dict[str, object],
    snapshot: dict[str, object],
    output_path: Path | str,
    initial_capital_usdt: Decimal | str = DEFAULT_INITIAL_CAPITAL_USDT,
) -> dict[str, object]:
    outcome = build_simulation_outcome(
        summary=summary,
        snapshot=snapshot,
        initial_capital_usdt=initial_capital_usdt,
    )
    target_path = Path(output_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(outcome, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    return outcome


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _to_decimal(value: Decimal | str | int | float | object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return ZERO


def _decimal_to_str(value: Decimal) -> str:
    return format(value, "f")
