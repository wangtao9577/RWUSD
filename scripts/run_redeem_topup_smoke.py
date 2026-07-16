import argparse
from collections import Counter
import json
from decimal import Decimal
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app.live_runner import LiveRunner
from src.domain.models import PortfolioSnapshot, ProfitBucket, SelectorSnapshot
from src.infra.logging import CompositeLogger, InMemoryLogger, JsonlFileLogger
from src.infra.simulation_report import write_runtime_summary
from src.portfolio.transfers import TransferPlanner
from src.risk.rules import RiskRuleSet


class RecordingAccountService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def redeem_rwusd(self, amount: str) -> dict[str, str]:
        payload = {"amount": amount}
        self.calls.append({"method": "redeem_rwusd", "payload": payload})
        return payload

    def transfer_spot_to_pm(self, asset: str, amount: str) -> dict[str, str]:
        payload = {"asset": asset, "amount": amount}
        self.calls.append({"method": "transfer_spot_to_pm", "payload": payload})
        return payload

    def transfer_pm_to_spot(self, asset: str, amount: str) -> dict[str, str]:
        payload = {"asset": asset, "amount": amount}
        self.calls.append({"method": "transfer_pm_to_spot", "payload": payload})
        return payload

    def subscribe_rwusd(self, amount: str) -> dict[str, str]:
        payload = {"amount": amount}
        self.calls.append({"method": "subscribe_rwusd", "payload": payload})
        return payload

    def get_um_hedge_positions(self) -> list[object]:
        return []

    def set_um_position_mode(self, dual_side_position: bool) -> dict[str, bool]:
        payload = {"dual_side_position": dual_side_position}
        self.calls.append({"method": "set_um_position_mode", "payload": payload})
        return payload

    def place_um_order(self, **kwargs) -> dict[str, object]:
        self.calls.append({"method": "place_um_order", "payload": dict(kwargs)})
        return dict(kwargs)

    def close_position(self, **kwargs) -> dict[str, object]:
        self.calls.append({"method": "close_position", "payload": dict(kwargs)})
        return dict(kwargs)


class NullStreamClient:
    def parse_user_stream_event(self, payload: dict) -> None:
        return None

    def run_user_stream_loop(self, **kwargs) -> int:
        return 0


class MemoryStateStore:
    def __init__(self) -> None:
        self.state = None

    def save_hedge_state(self, state) -> None:
        self.state = state

    def load_hedge_state(self):
        return self.state


class StaticSelector:
    def __init__(self, selected_symbol: str | None = None) -> None:
        self._selected_symbol = selected_symbol

    def select(
        self,
        current_symbol: str | None,
        rows: list[dict],
        minute_of_day: int | None = None,
    ) -> SelectorSnapshot:
        selected_symbols = [self._selected_symbol] if self._selected_symbol else []
        return SelectorSnapshot(
            selected_symbol=self._selected_symbol,
            selected_symbols=selected_symbols,
        )


def cli(argv: list[str] | None = None) -> dict[str, object]:
    parser = argparse.ArgumentParser(prog="run_redeem_topup_smoke")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--account-equity", default="1000")
    parser.add_argument("--available-balance", default="260")
    parser.add_argument("--uni-mmr", default="14")
    parser.add_argument("--current-drawdown", default="0.01")
    parser.add_argument("--rwusd-principal", default="120")
    parser.add_argument("--rwusd-redeemable", default="120")
    parser.add_argument("--pm-reserve", default="300")
    parser.add_argument("--soft-unimmr", default="12")
    parser.add_argument("--hard-unimmr", default="8")
    parser.add_argument("--redeem-unimmr", default="10")
    parser.add_argument("--min-redeem", default="10")
    parser.add_argument("--min-sweep", default="25")
    parser.add_argument("--target-notional", default="1000")
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "live_sim_runtime.jsonl"
    summary_path = output_dir / "runtime-summary.json"
    result_path = output_dir / "smoke-result.json"

    account_service = RecordingAccountService()
    memory_logger = InMemoryLogger()
    logger = CompositeLogger([JsonlFileLogger(log_path), memory_logger])
    state_store = MemoryStateStore()

    runner = LiveRunner(
        account_service=account_service,
        stream_client=NullStreamClient(),
        candidate_symbols=[str(args.symbol).upper()],
        target_notional=Decimal(args.target_notional),
        long_take_profit=Decimal("25"),
        short_take_profit=Decimal("25"),
        dry_run=False,
        state_store=state_store,
        risk_manager=RiskRuleSet(
            soft_unimmr=Decimal(args.soft_unimmr),
            hard_unimmr=Decimal(args.hard_unimmr),
            max_drawdown=Decimal("0.15"),
            redeem_unimmr=Decimal(args.redeem_unimmr),
            reserve_available_balance=Decimal(args.pm_reserve),
        ),
        transfer_planner=TransferPlanner(
            min_sweep=Decimal(args.min_sweep),
            pm_reserve=Decimal(args.pm_reserve),
            min_redeem=Decimal(args.min_redeem),
            redeem_unimmr=Decimal(args.redeem_unimmr),
        ),
        logger=logger,
    )
    runner._selector = StaticSelector(None)

    bucket_before = ProfitBucket(
        rwusd_principal=Decimal(args.rwusd_principal),
        rwusd_redeemable=Decimal(args.rwusd_redeemable),
    )
    runner._profit_bucket = bucket_before
    snapshot = PortfolioSnapshot(
        account_equity=Decimal(args.account_equity),
        available_balance=Decimal(args.available_balance),
        uni_mmr=Decimal(args.uni_mmr),
    )

    cycle_result = runner.run_cycle(
        rows=[],
        snapshot=snapshot,
        current_drawdown=Decimal(args.current_drawdown),
    )

    logger.log(
        level="INFO",
        message="smoke cycle completed",
        event="runtime.loop_completed",
        context={
            "loop_count": 1,
            "selected_symbols": [cycle_result.selected_symbol] if cycle_result.selected_symbol else [],
            "risk_reasons": [cycle_result.risk_reason] if cycle_result.risk_reason else [],
            "intent_actions": [cycle_result.intent.action] if cycle_result.intent.action else [],
            "profit_sweep_count": runner.profit_bucket.deposit_count,
            "redeem_topup_count": runner.profit_bucket.redeem_count,
            "rwusd_principal": str(runner.profit_bucket.rwusd_principal),
            "rwusd_interest_accrued": str(runner.profit_bucket.rwusd_interest_accrued),
        },
    )

    summary_payload = write_runtime_summary(
        log_path=log_path,
        output_path=summary_path,
    )
    result_payload = _build_result_payload(
        output_dir=output_dir,
        snapshot=snapshot,
        bucket_before=bucket_before,
        bucket_after=runner.profit_bucket,
        cycle_result=cycle_result,
        account_calls=account_service.calls,
        logger=memory_logger,
        summary_payload=summary_payload,
    )
    result_path.write_text(
        json.dumps(result_payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )

    return {
        "output_dir": str(output_dir),
        "log_path": str(log_path),
        "summary_path": str(summary_path),
        "result_path": str(result_path),
    }


def _build_result_payload(
    *,
    output_dir: Path,
    snapshot: PortfolioSnapshot,
    bucket_before: ProfitBucket,
    bucket_after: ProfitBucket,
    cycle_result,
    account_calls: list[dict[str, object]],
    logger: InMemoryLogger,
    summary_payload: dict[str, object],
) -> dict[str, object]:
    event_counts = Counter(record.event for record in logger.records if record.event)
    return {
        "output_dir": str(output_dir),
        "scenario": {
            "account_equity": str(snapshot.account_equity),
            "available_balance": str(snapshot.available_balance),
            "uni_mmr": str(snapshot.uni_mmr),
        },
        "run_cycle_result": {
            "selected_symbol": cycle_result.selected_symbol,
            "intent_action": cycle_result.intent.action,
            "risk_reason": cycle_result.risk_reason,
            "risk_should_pause": cycle_result.risk_should_pause,
            "risk_should_reduce": cycle_result.risk_should_reduce,
            "dry_run": cycle_result.dry_run,
        },
        "bucket_before": _serialize_bucket(bucket_before),
        "bucket_after": _serialize_bucket(bucket_after),
        "account_calls": account_calls,
        "event_counts": dict(event_counts),
        "events": [
            {
                "level": record.level,
                "message": record.message,
                "event": record.event,
                "context": _serialize_mapping(record.context),
            }
            for record in logger.records
        ],
        "summary": _serialize_mapping(summary_payload),
    }


def _serialize_bucket(bucket: ProfitBucket) -> dict[str, object]:
    return {
        "realized_pnl_total": str(bucket.realized_pnl_total),
        "realized_pnl_available_for_deposit": str(bucket.realized_pnl_available_for_deposit),
        "rwusd_principal": str(bucket.rwusd_principal),
        "rwusd_interest_accrued": str(bucket.rwusd_interest_accrued),
        "rwusd_redeemable": str(bucket.rwusd_redeemable),
        "harvest_count": bucket.harvest_count,
        "deposit_count": bucket.deposit_count,
        "redeem_count": bucket.redeem_count,
    }


def _serialize_mapping(payload: dict[str, object]) -> dict[str, object]:
    serialized: dict[str, object] = {}
    for key, value in payload.items():
        serialized[key] = _serialize_value(value)
    return serialized


def _serialize_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    return value


if __name__ == "__main__":
    cli()
