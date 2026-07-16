import json
from collections import Counter
from pathlib import Path


def build_runtime_summary(log_path: Path | str) -> dict[str, object]:
    target_path = Path(log_path)
    loop_completed_count = 0
    loop_retry_count = 0
    selected_symbol_counts: Counter[str] = Counter()
    risk_reason_counts: Counter[str] = Counter()
    intent_action_counts: Counter[str] = Counter()
    loop_rebalance_action_counts: Counter[str] = Counter()
    pending_detail_rebalance_action_counts: Counter[str] = Counter()
    profit_sweep_count = 0
    pending_detail_profit_sweep_count = 0
    redeem_topup_count = 0
    pending_detail_redeem_topup_count = 0
    rwusd_principal = "0"
    rwusd_interest_accrued = "0"
    harvest_buffer = "0"
    closed_loop_ready = False
    last_rebalance_action = None
    sweep_block_reason = None

    for line in target_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue

        payload = json.loads(line)
        event = payload.get("event")
        context = payload.get("context", {})

        if event == "runtime.loop_completed":
            loop_completed_count += 1
            for symbol in context.get("selected_symbols", []):
                if symbol:
                    selected_symbol_counts[symbol] += 1
            for reason in context.get("risk_reasons", []):
                if reason:
                    risk_reason_counts[reason] += 1
            for action in context.get("intent_actions", []):
                if action:
                    intent_action_counts[action] += 1
            if "rebalance_actions" in context:
                for action in context.get("rebalance_actions", []):
                    if action:
                        loop_rebalance_action_counts[action] += 1
            else:
                loop_rebalance_action_counts.update(
                    pending_detail_rebalance_action_counts
                )
            pending_detail_rebalance_action_counts.clear()
            if "profit_sweep_count" in context:
                profit_sweep_count += int(context.get("profit_sweep_count", 0) or 0)
            else:
                profit_sweep_count += pending_detail_profit_sweep_count
            pending_detail_profit_sweep_count = 0
            if "redeem_topup_count" in context:
                redeem_topup_count += int(context.get("redeem_topup_count", 0) or 0)
            else:
                redeem_topup_count += pending_detail_redeem_topup_count
            pending_detail_redeem_topup_count = 0
            rwusd_principal = str(context.get("rwusd_principal", rwusd_principal))
            rwusd_interest_accrued = str(
                context.get("rwusd_interest_accrued", rwusd_interest_accrued)
            )
            harvest_buffer = str(context.get("harvest_buffer", harvest_buffer))
            closed_loop_ready = bool(
                context.get("closed_loop_ready", closed_loop_ready)
            )
            last_rebalance_action = context.get(
                "last_rebalance_action",
                last_rebalance_action,
            )
            sweep_block_reason = context.get(
                "sweep_block_reason",
                sweep_block_reason,
            )
        elif event == "runtime.loop_retry":
            loop_retry_count += 1
        elif event == "live.rebalance_decision":
            action = context.get("action")
            if action:
                pending_detail_rebalance_action_counts[action] += 1
        elif event == "live.profit_sweep_executed":
            pending_detail_profit_sweep_count += 1
        elif event == "live.redeem_topup_executed":
            pending_detail_redeem_topup_count += 1

    loop_rebalance_action_counts.update(pending_detail_rebalance_action_counts)
    profit_sweep_count += pending_detail_profit_sweep_count
    redeem_topup_count += pending_detail_redeem_topup_count

    return {
        "log_path": str(target_path),
        "loop_completed_count": loop_completed_count,
        "loop_retry_count": loop_retry_count,
        "selected_symbol_counts": dict(selected_symbol_counts),
        "risk_reason_counts": dict(risk_reason_counts),
        "intent_action_counts": dict(intent_action_counts),
        "rebalance_action_counts": dict(loop_rebalance_action_counts),
        "profit_sweep_count": profit_sweep_count,
        "redeem_topup_count": redeem_topup_count,
        "rwusd_principal": rwusd_principal,
        "rwusd_interest_accrued": rwusd_interest_accrued,
        "harvest_buffer": harvest_buffer,
        "closed_loop_ready": closed_loop_ready,
        "last_rebalance_action": last_rebalance_action,
        "sweep_block_reason": sweep_block_reason,
    }


def write_runtime_summary(
    log_path: Path | str,
    output_path: Path | str,
) -> dict[str, object]:
    summary = build_runtime_summary(log_path)
    target_path = Path(output_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(summary, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    return summary
