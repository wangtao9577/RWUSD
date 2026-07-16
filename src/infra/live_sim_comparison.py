import json
from decimal import Decimal
from pathlib import Path


def build_live_sim_comparison_report(
    live_summary_path: Path | str,
    sim_summary_path: Path | str,
    live_snapshot_path: Path | str | None = None,
    sim_snapshot_path: Path | str | None = None,
) -> dict[str, object]:
    live_summary = _read_json(live_summary_path)
    sim_summary = _read_json(sim_summary_path)
    live_snapshot = _read_json(live_snapshot_path) if live_snapshot_path is not None else {}
    sim_snapshot = _read_json(sim_snapshot_path) if sim_snapshot_path is not None else {}

    selected_symbol_count_diffs = _diff_counter_mapping(
        live_summary.get("selected_symbol_counts", {}),
        sim_summary.get("selected_symbol_counts", {}),
    )
    rebalance_action_count_diffs = _diff_counter_mapping(
        live_summary.get("rebalance_action_counts", {}),
        sim_summary.get("rebalance_action_counts", {}),
    )
    profit_sweep_count_diff = _diff_int(
        live_summary.get("profit_sweep_count", 0),
        sim_summary.get("profit_sweep_count", 0),
    )
    redeem_topup_count_diff = _diff_int(
        live_summary.get("redeem_topup_count", 0),
        sim_summary.get("redeem_topup_count", 0),
    )

    snapshot_report = _build_snapshot_report(live_snapshot, sim_snapshot)
    matches = {
        "selected_symbol_counts": not selected_symbol_count_diffs,
        "rebalance_action_counts": not rebalance_action_count_diffs,
        "profit_sweep_count": profit_sweep_count_diff == 0,
        "redeem_topup_count": redeem_topup_count_diff == 0,
        "snapshot_selected_symbols": snapshot_report["selected_symbols"]["live"] == snapshot_report["selected_symbols"]["sim"],
        "snapshot_position_symbols": (
            not snapshot_report["position_symbols_only_in_live"]
            and not snapshot_report["position_symbols_only_in_sim"]
        ),
        "snapshot_account_metrics": not snapshot_report["account_metric_deltas"],
        "snapshot_market_rows": not snapshot_report["market_deltas"],
    }
    mismatches = [name for name, matched in matches.items() if not matched]

    return {
        "paths": {
            "live_summary_path": str(live_summary_path),
            "sim_summary_path": str(sim_summary_path),
            "live_snapshot_path": str(live_snapshot_path) if live_snapshot_path is not None else None,
            "sim_snapshot_path": str(sim_snapshot_path) if sim_snapshot_path is not None else None,
        },
        "summary": {
            "selected_symbol_count_diffs": selected_symbol_count_diffs,
            "rebalance_action_count_diffs": rebalance_action_count_diffs,
            "profit_sweep_count_diff": profit_sweep_count_diff,
            "redeem_topup_count_diff": redeem_topup_count_diff,
        },
        "snapshot": snapshot_report,
        "matches": matches,
        "mismatches": mismatches,
    }


def write_live_sim_comparison_report(
    live_summary_path: Path | str,
    sim_summary_path: Path | str,
    output_path: Path | str,
    live_snapshot_path: Path | str | None = None,
    sim_snapshot_path: Path | str | None = None,
) -> dict[str, object]:
    report = build_live_sim_comparison_report(
        live_summary_path=live_summary_path,
        sim_summary_path=sim_summary_path,
        live_snapshot_path=live_snapshot_path,
        sim_snapshot_path=sim_snapshot_path,
    )
    target_path = Path(output_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(report, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    return report


def _build_snapshot_report(
    live_snapshot: dict[str, object],
    sim_snapshot: dict[str, object],
) -> dict[str, object]:
    live_selected_symbols = list(live_snapshot.get("selected_symbols", []))
    sim_selected_symbols = list(sim_snapshot.get("selected_symbols", []))
    live_position_symbols = sorted(
        {
            position.get("symbol")
            for position in live_snapshot.get("positions", [])
            if position.get("symbol")
        }
    )
    sim_position_symbols = sorted(
        {
            position.get("symbol")
            for position in sim_snapshot.get("positions", [])
            if position.get("symbol")
        }
    )
    live_market_by_symbol = {
        row.get("symbol"): row
        for row in live_snapshot.get("market", [])
        if row.get("symbol")
    }
    sim_market_by_symbol = {
        row.get("symbol"): row
        for row in sim_snapshot.get("market", [])
        if row.get("symbol")
    }

    return {
        "selected_symbols": {
            "live": live_selected_symbols,
            "sim": sim_selected_symbols,
        },
        "position_symbols_only_in_live": sorted(set(live_position_symbols) - set(sim_position_symbols)),
        "position_symbols_only_in_sim": sorted(set(sim_position_symbols) - set(live_position_symbols)),
        "account_metric_deltas": _build_account_metric_deltas(
            live_snapshot.get("account", {}),
            sim_snapshot.get("account", {}),
        ),
        "market_deltas": _build_market_deltas(live_market_by_symbol, sim_market_by_symbol),
    }


def _build_account_metric_deltas(
    live_account: dict[str, object],
    sim_account: dict[str, object],
) -> dict[str, str]:
    deltas: dict[str, str] = {}
    for key in ("account_equity", "available_balance", "uni_mmr"):
        if key not in live_account or key not in sim_account:
            continue
        delta = _format_decimal_delta(live_account[key], sim_account[key])
        if delta != "0":
            deltas[key] = delta
    return deltas


def _build_market_deltas(
    live_market_by_symbol: dict[str, dict[str, object]],
    sim_market_by_symbol: dict[str, dict[str, object]],
) -> dict[str, dict[str, str]]:
    deltas: dict[str, dict[str, str]] = {}
    for symbol in sorted(set(live_market_by_symbol) & set(sim_market_by_symbol)):
        live_row = live_market_by_symbol[symbol]
        sim_row = sim_market_by_symbol[symbol]
        row_delta: dict[str, str] = {}
        for field_name in ("close_price", "funding_rate"):
            if field_name not in live_row or field_name not in sim_row:
                continue
            delta = _format_decimal_delta(live_row[field_name], sim_row[field_name])
            if delta != "0":
                row_delta[f"{field_name}_delta"] = delta
        if row_delta:
            deltas[symbol] = row_delta
    return deltas


def _diff_counter_mapping(
    live_values: dict[str, object],
    sim_values: dict[str, object],
) -> dict[str, int]:
    diffs: dict[str, int] = {}
    for key in sorted(set(live_values) | set(sim_values)):
        live_value = int(live_values.get(key, 0) or 0)
        sim_value = int(sim_values.get(key, 0) or 0)
        diff = sim_value - live_value
        if diff != 0:
            diffs[key] = diff
    return diffs


def _diff_int(live_value: object, sim_value: object) -> int:
    return int(sim_value or 0) - int(live_value or 0)


def _format_decimal_delta(live_value: object, sim_value: object) -> str:
    delta = Decimal(str(sim_value)) - Decimal(str(live_value))
    normalized = delta.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text == "-0":
        return "0"
    return text


def _read_json(path: Path | str) -> dict[str, object]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
