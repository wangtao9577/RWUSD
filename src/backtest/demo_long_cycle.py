from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import json

from src.backtest.engine import BacktestEngine, BacktestRuntimeState
from src.domain.enums import PositionSide
from src.domain.models import ProfitBucket
from src.infra.simulation_outcome import build_simulation_outcome


ZERO = Decimal("0")
DEFAULT_INITIAL_CAPITAL_USDT = Decimal("10000")
DEFAULT_ELAPSED_HOURS_PER_CYCLE = Decimal("24")
DEFAULT_CYCLE_COUNT = 12


@dataclass(slots=True)
class DemoCycle:
    cycle_no: int
    minute_of_day: int | None
    allow_restore_now: bool
    rows: list[dict[str, object]]
    snapshot: dict[str, object]


def build_demo_cycles() -> list[DemoCycle]:
    cycles: list[DemoCycle] = []
    take_profit_specs = {
        2: (PositionSide.LONG, Decimal("96")),
        3: (PositionSide.SHORT, Decimal("89")),
        5: (PositionSide.LONG, Decimal("87")),
        6: (PositionSide.SHORT, Decimal("84")),
        8: (PositionSide.LONG, Decimal("83")),
        9: (PositionSide.SHORT, Decimal("82")),
        12: (PositionSide.LONG, Decimal("65")),
    }
    base_prices = {
        "BTCUSDT": Decimal("60000"),
        "ETHUSDT": Decimal("3200"),
        "SOLUSDT": Decimal("165"),
    }

    for cycle_no in range(1, DEFAULT_CYCLE_COUNT + 1):
        side, gross_profit = take_profit_specs.get(cycle_no, (None, ZERO))
        long_unrealized = ZERO
        short_unrealized = ZERO
        if side == PositionSide.LONG:
            long_unrealized = gross_profit
        elif side == PositionSide.SHORT:
            short_unrealized = gross_profit

        rows = [
            _build_row(
                symbol="BTCUSDT",
                close=base_prices["BTCUSDT"] + Decimal(cycle_no * 180),
                liquidity=Decimal("0.96"),
                volatility=Decimal("0.92"),
                funding=Decimal("0.84"),
                margin=Decimal("0.95"),
                long_unrealized=long_unrealized,
                short_unrealized=short_unrealized,
            ),
            _build_row(
                symbol="ETHUSDT",
                close=base_prices["ETHUSDT"] + Decimal(cycle_no * 16),
                liquidity=Decimal("0.84"),
                volatility=Decimal("0.77"),
                funding=Decimal("0.71"),
                margin=Decimal("0.81"),
                long_unrealized=Decimal("12"),
                short_unrealized=Decimal("10"),
            ),
            _build_row(
                symbol="SOLUSDT",
                close=base_prices["SOLUSDT"] + Decimal(cycle_no * 2),
                liquidity=Decimal("0.73"),
                volatility=Decimal("0.69"),
                funding=Decimal("0.65"),
                margin=Decimal("0.72"),
                long_unrealized=Decimal("8"),
                short_unrealized=Decimal("7"),
            ),
        ]
        cycles.append(
            DemoCycle(
                cycle_no=cycle_no,
                minute_of_day=(cycle_no - 1) * 120,
                allow_restore_now=cycle_no not in {3, 6, 9},
                rows=rows,
                snapshot={
                    "account_equity": Decimal("12000"),
                    "available_balance": Decimal("1500"),
                    "uni_mmr": Decimal("12.8"),
                },
            )
        )
    return cycles


def run_demo_report() -> dict[str, object]:
    engine = BacktestEngine()
    runtime_state = BacktestRuntimeState(
        profit_bucket=ProfitBucket(
            rwusd_principal=DEFAULT_INITIAL_CAPITAL_USDT,
            rwusd_redeemable=DEFAULT_INITIAL_CAPITAL_USDT,
        )
    )
    cycles = build_demo_cycles()
    history: list[dict[str, object]] = []
    selected_symbol_counts: Counter[str] = Counter()

    aggregate_take_profit = 0
    aggregate_restore_now = 0
    aggregate_restore_later = 0
    aggregate_profit_sweep = 0
    aggregate_realized_pnl = Decimal("0")

    for index, cycle in enumerate(cycles):
        result = engine.run(
            rows=cycle.rows,
            snapshot=cycle.snapshot,
            elapsed_hours=Decimal("0") if index == 0 else DEFAULT_ELAPSED_HOURS_PER_CYCLE,
            allow_restore_now=cycle.allow_restore_now,
            minute_of_day=cycle.minute_of_day,
            runtime_state=runtime_state,
        )
        runtime_state = result.runtime_state or runtime_state
        if result.current_symbol:
            selected_symbol_counts[result.current_symbol] += 1
        aggregate_take_profit += result.take_profit_count
        aggregate_restore_now += result.restore_now_count
        aggregate_restore_later += result.restore_later_count
        aggregate_profit_sweep += result.profit_sweep_count
        aggregate_realized_pnl += result.realized_pnl
        history.append(
            {
                "cycle_no": cycle.cycle_no,
                "selected_symbol": result.current_symbol,
                "phase": result.phase.name if result.phase is not None else None,
                "take_profit_count": result.take_profit_count,
                "restore_now_count": result.restore_now_count,
                "restore_later_count": result.restore_later_count,
                "profit_sweep_count": result.profit_sweep_count,
                "realized_pnl": _decimal_to_str(result.realized_pnl),
                "rwusd_principal": _decimal_to_str(result.rwusd_principal),
                "rwusd_interest_accrued": _decimal_to_str(result.rwusd_interest_accrued),
            }
        )

    ending_total_value = runtime_state.profit_bucket.rwusd_principal + runtime_state.profit_bucket.rwusd_interest_accrued
    net_gain = ending_total_value - DEFAULT_INITIAL_CAPITAL_USDT
    runtime_days = Decimal(max(0, len(cycles) - 1))
    monthly_return_pct = Decimal("0")
    annualized_return_pct = Decimal("0")
    if runtime_days > ZERO:
        total_return_ratio = net_gain / DEFAULT_INITIAL_CAPITAL_USDT
        monthly_return_pct = total_return_ratio * (Decimal("30") / runtime_days) * Decimal("100")
        annualized_return_pct = total_return_ratio * (Decimal("365") / runtime_days) * Decimal("100")

    summary = {
        "initial_capital_usdt": _decimal_to_str(DEFAULT_INITIAL_CAPITAL_USDT),
        "cycle_count": len(cycles),
        "runtime_days": int(runtime_days),
        "selected_symbol_counts": dict(selected_symbol_counts),
        "final_symbol": runtime_state.current_symbol,
        "final_phase": runtime_state.phase.name,
        "take_profit_total": aggregate_take_profit,
        "restore_now_total": aggregate_restore_now,
        "restore_later_total": aggregate_restore_later,
        "profit_sweep_total": aggregate_profit_sweep,
        "realized_pnl_total": _decimal_to_str(aggregate_realized_pnl),
        "rwusd_principal": _decimal_to_str(runtime_state.profit_bucket.rwusd_principal),
        "rwusd_interest_accrued": _decimal_to_str(runtime_state.profit_bucket.rwusd_interest_accrued),
        "ending_total_value": _decimal_to_str(ending_total_value),
        "net_gain": _decimal_to_str(net_gain),
        "total_return_pct": round(float(net_gain / DEFAULT_INITIAL_CAPITAL_USDT * Decimal("100")), 4),
        "monthly_return_pct_linear": round(float(monthly_return_pct), 4),
        "annualized_return_pct_linear": round(float(annualized_return_pct), 4),
    }

    outcome = build_simulation_outcome(
        summary={
            "selected_symbol_counts": dict(selected_symbol_counts),
            "risk_reason_counts": {},
            "rebalance_action_counts": {
                "restore_now": aggregate_restore_now,
                "restore_later": aggregate_restore_later,
                "hold": max(0, len(cycles) - aggregate_take_profit),
            },
            "profit_sweep_count": aggregate_profit_sweep,
            "redeem_topup_count": 0,
            "rwusd_principal": summary["rwusd_principal"],
            "rwusd_interest_accrued": summary["rwusd_interest_accrued"],
        },
        snapshot={
            "account": {"uni_mmr": "12.8"},
            "strategy": {"harvest_buffer": "0"},
        },
    )

    return {
        "scenario": {
            "name": "rwusd_long_cycle_demo",
            "cycle_count": len(cycles),
            "elapsed_hours_per_cycle": str(DEFAULT_ELAPSED_HOURS_PER_CYCLE),
        },
        "summary": summary,
        "history": history,
        "outcome": outcome,
        "pdf_alignment_note": "演示模拟，口径对齐 PDF 核心闭环，不代表历史实盘回放。",
    }


def write_demo_report(output_dir: Path | str) -> dict[str, object]:
    report = run_demo_report()
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    (target_dir / "report.md").write_text(
        render_demo_report_markdown(report),
        encoding="utf-8",
    )
    return report


def render_demo_report_markdown(report: dict[str, object]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# RWUSD 长周期演示报告",
        "",
        f"- 周期数: {summary.get('cycle_count', 0)}",
        f"- 最终标的: {summary.get('final_symbol', '-')}",
        f"- 最终阶段: {summary.get('final_phase', '-')}",
        f"- 止盈: {summary.get('take_profit_total', 0)}",
        f"- 立即补腿: {summary.get('restore_now_total', 0)}",
        f"- 延迟补腿: {summary.get('restore_later_total', 0)}",
        f"- 利润沉淀: {summary.get('profit_sweep_total', 0)}",
        f"- RWUSD 本金: {summary.get('rwusd_principal', '0')}",
        f"- RWUSD 利息: {summary.get('rwusd_interest_accrued', '0')}",
        f"- 净收益: {summary.get('net_gain', '0')}",
        f"- 月化: {summary.get('monthly_return_pct_linear', 0.0)}%",
        f"- 年化: {summary.get('annualized_return_pct_linear', 0.0)}%",
    ]
    return "\n".join(lines) + "\n"


def _build_row(
    *,
    symbol: str,
    close: Decimal,
    liquidity: Decimal,
    volatility: Decimal,
    funding: Decimal,
    margin: Decimal,
    long_unrealized: Decimal,
    short_unrealized: Decimal,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "close": close,
        "liquidity": liquidity,
        "volatility": volatility,
        "funding": funding,
        "margin": margin,
        "blocked": False,
        "long_unrealized": long_unrealized,
        "short_unrealized": short_unrealized,
    }


def _decimal_to_str(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
