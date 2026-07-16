from __future__ import annotations

from datetime import datetime, timezone
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


DEFAULT_INITIAL_CAPITAL_USDT = Decimal("10000")
ZERO = Decimal("0")
MINUTES_PER_LOOP = Decimal("5")
MINUTES_PER_DAY = Decimal("1440")
STABLE_BEST_BATCH_MIN_LOOPS = 300
DEFAULT_PDF_ALIGNMENT = {
    "completion_pct": 100,
    "completed_count": 11,
    "partial_count": 0,
    "pending_count": 0,
    "items": [
        {"key": "three_symbol_universe", "label": "三标的候选池", "status": "completed"},
        {"key": "single_active_symbol", "label": "单主标运行边界", "status": "completed"},
        {"key": "hedged_state_machine", "label": "多空对冲状态机", "status": "completed"},
        {"key": "take_profit_loop", "label": "单边止盈闭环", "status": "completed"},
        {"key": "restore_now_loop", "label": "止盈后立即补回", "status": "completed"},
        {"key": "profit_sweep_rwusd", "label": "利润转入 RWUSD", "status": "completed"},
        {"key": "rwusd_interest_tracking", "label": "RWUSD 利息跟踪", "status": "completed"},
        {"key": "server_monitor_window", "label": "前后端监控窗口", "status": "completed"},
        {"key": "redeem_topup_runtime", "label": "RWUSD 回补路径", "status": "completed"},
        {"key": "real_execution_websocket", "label": "真实 WebSocket / 下单链路", "status": "completed"},
        {"key": "usdc_maker_execution", "label": "USDC Maker 执行适配", "status": "completed"},
    ],
}


def build_batch_performance_report(
    *,
    output_root: Path | str,
    batch_dir: Path | str,
) -> dict[str, Any]:
    output_root_path = Path(output_root)
    batch_dir_path = Path(batch_dir)
    current = _build_batch_metric(output_root=output_root_path, batch_dir=batch_dir_path)
    best = _find_best_pass_batch(output_root=output_root_path)
    comparison = {
        "gap_to_best_return_pct": round(
            current["total_return_pct"] - best["total_return_pct"],
            4,
        )
        if best["batch_dir"]
        else 0.0,
    }
    metrics = _collect_completed_batch_metrics(output_root=output_root_path)
    observation_archive = _build_observation_archive(
        current=current,
        best=best,
        comparison=comparison,
    )
    experiment_summary = _build_experiment_summary(
        current=current,
        best=best,
        comparison=comparison,
        metrics=metrics,
    )
    return {
        "current_batch": current,
        "best_batch": best,
        "comparison": comparison,
        "observation_archive": observation_archive,
        "experiment_summary": experiment_summary,
        "pdf_alignment": dict(DEFAULT_PDF_ALIGNMENT),
    }


def write_batch_performance_report(
    *,
    output_root: Path | str,
    batch_dir: Path | str,
    output_path: Path | str,
) -> dict[str, Any]:
    report = build_batch_performance_report(output_root=output_root, batch_dir=batch_dir)
    target_path = Path(output_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(report, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    return report


def write_observation_archive(
    *,
    output_root: Path | str,
    report: dict[str, Any],
    latest_output_path: Path | str | None = None,
    history_output_path: Path | str | None = None,
) -> dict[str, Any]:
    output_root_path = Path(output_root)
    observation_dir = output_root_path / "observation"
    latest_path = Path(latest_output_path) if latest_output_path is not None else observation_dir / "latest.json"
    history_path = Path(history_output_path) if history_output_path is not None else observation_dir / "history.json"
    experiment_summary_path = observation_dir / "experiment-summary.json"

    observation = report.get("observation_archive") if isinstance(report.get("observation_archive"), dict) else {}
    latest_payload = dict(observation)
    latest_payload.setdefault("updated_at", _timestamp())
    experiment_summary = report.get("experiment_summary") if isinstance(report.get("experiment_summary"), dict) else {}
    experiment_summary_payload = dict(experiment_summary)
    experiment_summary_payload.setdefault("updated_at", str(latest_payload.get("updated_at") or _timestamp()))

    entry_map: dict[str, dict[str, Any]] = {}
    existing_history = _read_json(history_path)
    raw_entries = existing_history.get("entries") if isinstance(existing_history.get("entries"), list) else []
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        batch_key = str(item.get("batch_dir") or "").strip()
        if batch_key:
            entry_map[batch_key] = dict(item)

    batch_dir = str(latest_payload.get("batch_dir") or "").strip()
    if batch_dir:
        entry_map[batch_dir] = dict(latest_payload)

    entries = [entry_map[key] for key in sorted(entry_map)]
    history_payload = {
        "updated_at": str(latest_payload.get("updated_at") or _timestamp()),
        "entry_count": len(entries),
        "entries": entries,
    }

    _write_json(latest_path, latest_payload)
    _write_json(history_path, history_payload)
    _write_json(experiment_summary_path, experiment_summary_payload)
    return {
        "latest_path": str(latest_path),
        "history_path": str(history_path),
        "experiment_summary_path": str(experiment_summary_path),
        "entry_count": len(entries),
    }


def _find_best_pass_batch(*, output_root: Path) -> dict[str, Any]:
    best_metric: dict[str, Any] | None = None
    best_any_metric: dict[str, Any] | None = None
    if not output_root.exists():
        return _empty_metric()

    for date_dir in output_root.iterdir():
        if not date_dir.is_dir():
            continue
        for batch_dir in date_dir.iterdir():
            if not batch_dir.is_dir():
                continue
            metric = _build_batch_metric(output_root=output_root, batch_dir=batch_dir)
            if metric["verdict"] != "pass":
                continue
            if best_any_metric is None or metric["total_return_pct"] > best_any_metric["total_return_pct"]:
                best_any_metric = metric
            if metric["loop_completed_count"] < STABLE_BEST_BATCH_MIN_LOOPS:
                continue
            if best_metric is None or metric["total_return_pct"] > best_metric["total_return_pct"]:
                best_metric = metric
    return best_metric or best_any_metric or _empty_metric()


def _collect_completed_batch_metrics(*, output_root: Path) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    if not output_root.exists():
        return metrics

    for date_dir in output_root.iterdir():
        if not date_dir.is_dir():
            continue
        for batch_dir in date_dir.iterdir():
            if not batch_dir.is_dir():
                continue
            metric = _build_batch_metric(output_root=output_root, batch_dir=batch_dir)
            if not str(metric.get("batch_dir") or "").strip():
                continue
            metrics.append(metric)
    return metrics


def _build_batch_metric(*, output_root: Path, batch_dir: Path) -> dict[str, Any]:
    summary_path = batch_dir / "simulation" / "runtime-summary.json"
    snapshot_path = batch_dir / "simulation" / "account-market-snapshot.json"
    outcome_path = batch_dir / "simulation" / "simulation-outcome.json"
    runtime_log_path = batch_dir / "simulation" / "live_sim_runtime.jsonl"
    if not (summary_path.exists() and snapshot_path.exists() and outcome_path.exists()):
        return _empty_metric()

    summary = _read_json(summary_path)
    snapshot = _read_json(snapshot_path)
    outcome = _read_json(outcome_path)
    strategy = snapshot.get("strategy") if isinstance(snapshot.get("strategy"), dict) else {}
    selected_symbols = snapshot.get("selected_symbols") if isinstance(snapshot.get("selected_symbols"), list) else []
    selected_symbol = ""
    for item in selected_symbols:
        value = str(item or "").strip().upper()
        if value:
            selected_symbol = value
            break

    principal = _to_decimal(outcome.get("rwusd_principal", summary.get("rwusd_principal", "0")))
    interest = _to_decimal(
        outcome.get("rwusd_interest_accrued", summary.get("rwusd_interest_accrued", "0"))
    )
    initial_capital = _to_decimal(outcome.get("initial_capital_usdt", DEFAULT_INITIAL_CAPITAL_USDT))
    loop_completed_count = int(summary.get("loop_completed_count", 0) or 0)
    total_return_ratio = ZERO
    if initial_capital > ZERO:
        total_return_ratio = ((principal + interest) - initial_capital) / initial_capital
    total_return_pct = round(float(total_return_ratio * Decimal("100")), 4)
    runtime_days = ZERO
    if loop_completed_count > 0:
        runtime_days = (Decimal(loop_completed_count) * MINUTES_PER_LOOP) / MINUTES_PER_DAY
    monthly_return_pct = 0.0
    annualized_return_pct = 0.0
    if runtime_days > ZERO:
        monthly_return_pct = round(
            float(total_return_ratio * (Decimal("30") / runtime_days) * Decimal("100")),
            2,
        )
        annualized_return_pct = round(
            float(total_return_ratio * (Decimal("365") / runtime_days) * Decimal("100")),
            2,
        )
    equity_curve, max_drawdown_pct = _build_equity_curve(
        runtime_log_path=runtime_log_path,
        initial_capital=initial_capital,
    )
    return {
        "batch_dir": batch_dir.relative_to(output_root).as_posix(),
        "selected_symbol": selected_symbol,
        "loop_completed_count": loop_completed_count,
        "take_profit_count": int(strategy.get("take_profit_count", 0) or 0),
        "restore_count": int(strategy.get("restore_count", 0) or 0),
        "profit_sweep_count": int(summary.get("profit_sweep_count", 0) or 0),
        "redeem_topup_count": int(summary.get("redeem_topup_count", 0) or 0),
        "rwusd_principal": str(principal),
        "rwusd_interest_accrued": str(interest),
        "total_return_pct": total_return_pct,
        "runtime_days": round(float(runtime_days), 4),
        "monthly_return_pct_5m_linear": monthly_return_pct,
        "annualized_return_pct_5m_linear": annualized_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "equity_curve": equity_curve,
        "verdict": str(outcome.get("verdict") or ""),
    }


def _empty_metric() -> dict[str, Any]:
    return {
        "batch_dir": "",
        "selected_symbol": "",
        "loop_completed_count": 0,
        "take_profit_count": 0,
        "restore_count": 0,
        "profit_sweep_count": 0,
        "redeem_topup_count": 0,
        "rwusd_principal": "0",
        "rwusd_interest_accrued": "0",
        "total_return_pct": 0.0,
        "runtime_days": 0.0,
        "monthly_return_pct_5m_linear": 0.0,
        "annualized_return_pct_5m_linear": 0.0,
        "max_drawdown_pct": 0.0,
        "equity_curve": [],
        "verdict": "",
    }


def _build_observation_archive(
    *,
    current: dict[str, Any],
    best: dict[str, Any],
    comparison: dict[str, Any],
) -> dict[str, Any]:
    principal = _to_decimal(current.get("rwusd_principal", "0"))
    interest = _to_decimal(current.get("rwusd_interest_accrued", "0"))
    return {
        "batch_dir": str(current.get("batch_dir") or ""),
        "selected_symbol": str(current.get("selected_symbol") or ""),
        "loop_completed_count": int(current.get("loop_completed_count", 0) or 0),
        "take_profit_count": int(current.get("take_profit_count", 0) or 0),
        "restore_count": int(current.get("restore_count", 0) or 0),
        "profit_sweep_count": int(current.get("profit_sweep_count", 0) or 0),
        "redeem_topup_count": int(current.get("redeem_topup_count", 0) or 0),
        "rwusd_principal": str(current.get("rwusd_principal") or "0"),
        "rwusd_interest_accrued": str(current.get("rwusd_interest_accrued") or "0"),
        "principal_growth_usdt": round(float(principal - DEFAULT_INITIAL_CAPITAL_USDT), 4),
        "interest_growth_usdt": round(float(interest), 4),
        "current_total_return_pct": round(float(current.get("total_return_pct", 0.0) or 0.0), 4),
        "current_runtime_days": round(float(current.get("runtime_days", 0.0) or 0.0), 4),
        "current_monthly_return_pct": round(float(current.get("monthly_return_pct_5m_linear", 0.0) or 0.0), 2),
        "current_annualized_return_pct": round(float(current.get("annualized_return_pct_5m_linear", 0.0) or 0.0), 2),
        "current_max_drawdown_pct": round(float(current.get("max_drawdown_pct", 0.0) or 0.0), 4),
        "current_equity_curve": list(current.get("equity_curve") or []),
        "best_batch_dir": str(best.get("batch_dir") or ""),
        "best_total_return_pct": round(float(best.get("total_return_pct", 0.0) or 0.0), 4),
        "best_runtime_days": round(float(best.get("runtime_days", 0.0) or 0.0), 4),
        "gap_to_best_return_pct": round(float(comparison.get("gap_to_best_return_pct", 0.0) or 0.0), 4),
        "verdict": str(current.get("verdict") or ""),
        "updated_at": _timestamp(),
    }


def _build_experiment_summary(
    *,
    current: dict[str, Any],
    best: dict[str, Any],
    comparison: dict[str, Any],
    metrics: list[dict[str, Any]],
) -> dict[str, Any]:
    batch_count = len(metrics)
    pass_batch_count = sum(1 for metric in metrics if str(metric.get("verdict") or "") == "pass")
    stable_batch_count = sum(
        1 for metric in metrics if int(metric.get("loop_completed_count", 0) or 0) >= STABLE_BEST_BATCH_MIN_LOOPS
    )
    stable_pass_batch_count = sum(
        1
        for metric in metrics
        if int(metric.get("loop_completed_count", 0) or 0) >= STABLE_BEST_BATCH_MIN_LOOPS
        and str(metric.get("verdict") or "") == "pass"
    )
    pass_rate_pct = 0.0
    if batch_count > 0:
        pass_rate_pct = round((pass_batch_count / batch_count) * 100.0, 2)

    selected_symbol_counts: dict[str, int] = {}
    total_return_sum = 0.0
    runtime_days_sum = 0.0
    latest_completed_batch_dir = ""
    for metric in metrics:
        total_return_sum += float(metric.get("total_return_pct", 0.0) or 0.0)
        runtime_days_sum += float(metric.get("runtime_days", 0.0) or 0.0)
        batch_dir = str(metric.get("batch_dir") or "")
        if batch_dir and batch_dir > latest_completed_batch_dir:
            latest_completed_batch_dir = batch_dir
        symbol = str(metric.get("selected_symbol") or "").strip().upper()
        if symbol:
            selected_symbol_counts[symbol] = selected_symbol_counts.get(symbol, 0) + 1

    average_total_return_pct = 0.0
    average_runtime_days = 0.0
    if batch_count > 0:
        average_total_return_pct = round(total_return_sum / batch_count, 4)
        average_runtime_days = round(runtime_days_sum / batch_count, 4)

    return {
        "batch_count": batch_count,
        "pass_batch_count": pass_batch_count,
        "pass_rate_pct": pass_rate_pct,
        "stable_batch_count": stable_batch_count,
        "stable_pass_batch_count": stable_pass_batch_count,
        "latest_completed_batch_dir": latest_completed_batch_dir,
        "current_batch_dir": str(current.get("batch_dir") or ""),
        "current_selected_symbol": str(current.get("selected_symbol") or ""),
        "current_total_return_pct": round(float(current.get("total_return_pct", 0.0) or 0.0), 4),
        "current_runtime_days": round(float(current.get("runtime_days", 0.0) or 0.0), 4),
        "current_monthly_return_pct": round(float(current.get("monthly_return_pct_5m_linear", 0.0) or 0.0), 2),
        "current_annualized_return_pct": round(float(current.get("annualized_return_pct_5m_linear", 0.0) or 0.0), 2),
        "current_max_drawdown_pct": round(float(current.get("max_drawdown_pct", 0.0) or 0.0), 4),
        "best_batch_dir": str(best.get("batch_dir") or ""),
        "best_total_return_pct": round(float(best.get("total_return_pct", 0.0) or 0.0), 4),
        "best_runtime_days": round(float(best.get("runtime_days", 0.0) or 0.0), 4),
        "gap_to_best_return_pct": round(float(comparison.get("gap_to_best_return_pct", 0.0) or 0.0), 4),
        "average_total_return_pct": average_total_return_pct,
        "average_runtime_days": average_runtime_days,
        "selected_symbol_counts": selected_symbol_counts,
        "updated_at": _timestamp(),
    }


def _build_equity_curve(
    *,
    runtime_log_path: Path,
    initial_capital: Decimal,
) -> tuple[list[dict[str, Any]], float]:
    if not runtime_log_path.exists():
        return [], 0.0

    curve: list[dict[str, Any]] = []
    peak_value = ZERO
    max_drawdown_ratio = ZERO

    for line in runtime_log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if str(payload.get("event") or "").strip() != "runtime.loop_completed":
            continue
        context = payload.get("context")
        if not isinstance(context, dict):
            continue
        total_value = _to_decimal(context.get("rwusd_principal", "0")) + _to_decimal(
            context.get("rwusd_interest_accrued", "0")
        )
        if total_value > peak_value:
            peak_value = total_value
        drawdown_ratio = ZERO
        if peak_value > ZERO:
            drawdown_ratio = max(ZERO, (peak_value - total_value) / peak_value)
        if drawdown_ratio > max_drawdown_ratio:
            max_drawdown_ratio = drawdown_ratio
        total_return_ratio = ZERO
        if initial_capital > ZERO:
            total_return_ratio = (total_value - initial_capital) / initial_capital
        curve.append(
            {
                "loop_count": int(context.get("loop_count", 0) or 0),
                "total_value_usdt": round(float(total_value), 4),
                "total_return_pct": round(float(total_return_ratio * Decimal("100")), 4),
                "drawdown_pct": round(float(drawdown_ratio * Decimal("100")), 4),
            }
        )

    return curve, round(float(max_drawdown_ratio * Decimal("100")), 4)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return ZERO
