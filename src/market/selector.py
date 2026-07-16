from decimal import Decimal
import typing

from typing import TypedDict
from typing_extensions import NotRequired as _typing_extensions_not_required

from src.domain.models import SelectorSnapshot, SymbolScore
from src.market.metrics import weighted_symbol_score


def _resolve_not_required():
    not_required = getattr(typing, "NotRequired", None)
    if not_required is not None:
        return not_required
    return _typing_extensions_not_required


NotRequired = _resolve_not_required()


class SelectorRow(TypedDict):
    symbol: str
    liquidity: Decimal
    volatility: Decimal
    funding: Decimal
    margin: Decimal
    blocked: bool
    execution_cost_bps: NotRequired[Decimal]
    preferred_execution_symbol: NotRequired[str]


EXECUTION_COST_WEIGHT = Decimal("0.05")
MAX_EXECUTION_COST_BPS = Decimal("5")


class SymbolSelector:
    def __init__(
        self,
        switch_threshold: Decimal,
        eval_interval_minutes: int = 15,
        switch_cooldown_minutes: int = 0,
    ) -> None:
        self._switch_threshold = switch_threshold
        self._eval_interval_minutes = max(1, eval_interval_minutes)
        self._switch_cooldown_minutes = max(0, switch_cooldown_minutes)

    def select(
        self,
        current_symbol: str | None,
        rows: list[SelectorRow],
        minute_of_day: int | None = None,
        last_switch_minute: int | None = None,
    ) -> SelectorSnapshot:
        ranked = self._rank_scores(rows)
        if self._should_hold_current_symbol(
            current_symbol=current_symbol,
            ranked=ranked,
            minute_of_day=minute_of_day,
            last_switch_minute=last_switch_minute,
        ):
            return SelectorSnapshot(
                scores=ranked,
                selected_symbol=current_symbol,
                selected_symbols=[current_symbol] if current_symbol is not None else [],
                cooldown_symbol=current_symbol,
            )
        selected = self._choose_selected_symbol(current_symbol=current_symbol, ranked=ranked)

        return SelectorSnapshot(
            scores=ranked,
            selected_symbol=selected,
            selected_symbols=[selected] if selected is not None else [],
        )

    def select_many(self, rows: list[SelectorRow], limit: int) -> SelectorSnapshot:
        ranked = self._rank_scores(rows)
        selected_symbols = [
            item.symbol
            for item in ranked
            if item.reject_reason is None
        ][: max(0, limit)]
        selected_symbol = selected_symbols[0] if selected_symbols else None
        return SelectorSnapshot(
            scores=ranked,
            selected_symbol=selected_symbol,
            selected_symbols=selected_symbols,
        )

    def _rank_scores(self, rows: list[SelectorRow]) -> list[SymbolScore]:
        scores = [self._build_score(row) for row in rows]
        return sorted(scores, key=lambda item: item.score, reverse=True)

    def _build_score(self, row: SelectorRow) -> SymbolScore:
        reject_reason = "blocked" if row["blocked"] else None
        execution_cost_bps = Decimal(str(row.get("execution_cost_bps", MAX_EXECUTION_COST_BPS)))
        execution_cost_score = _execution_cost_score(execution_cost_bps)
        total = Decimal("0") if reject_reason else weighted_symbol_score(
            row["liquidity"],
            row["volatility"],
            row["funding"],
            row["margin"],
        ) + (execution_cost_score * EXECUTION_COST_WEIGHT)
        return SymbolScore(
            symbol=row["symbol"],
            score=total,
            liquidity_score=row["liquidity"],
            volatility_score=row["volatility"],
            funding_score=row["funding"],
            margin_efficiency_score=row["margin"],
            execution_cost_score=execution_cost_score,
            execution_cost_bps=execution_cost_bps,
            preferred_execution_symbol=row.get("preferred_execution_symbol", row["symbol"]),
            reject_reason=reject_reason,
        )

    def _choose_selected_symbol(
        self,
        current_symbol: str | None,
        ranked: list[SymbolScore],
    ) -> str | None:
        best = next((item for item in ranked if item.reject_reason is None), None)
        current = next((item for item in ranked if item.symbol == current_symbol), None)

        if current and current.reject_reason is None and best:
            if best.symbol != current.symbol and (best.score - current.score) < self._switch_threshold:
                return current.symbol
            return best.symbol
        if best:
            return best.symbol
        return None

    def _should_hold_current_symbol(
        self,
        current_symbol: str | None,
        ranked: list[SymbolScore],
        minute_of_day: int | None,
        last_switch_minute: int | None,
    ) -> bool:
        if current_symbol is None or minute_of_day is None:
            return False
        current = next((item for item in ranked if item.symbol == current_symbol), None)
        if current is None or current.reject_reason is not None:
            return False
        if minute_of_day % self._eval_interval_minutes != 0:
            return True
        return self._is_switch_cooldown_active(
            minute_of_day=minute_of_day,
            last_switch_minute=last_switch_minute,
        )

    def _is_switch_cooldown_active(
        self,
        minute_of_day: int,
        last_switch_minute: int | None,
    ) -> bool:
        if self._switch_cooldown_minutes <= 0 or last_switch_minute is None:
            return False
        elapsed_minutes = (minute_of_day - last_switch_minute) % (24 * 60)
        return elapsed_minutes < self._switch_cooldown_minutes


def _execution_cost_score(execution_cost_bps: Decimal) -> Decimal:
    normalized = MAX_EXECUTION_COST_BPS - execution_cost_bps
    if normalized <= Decimal("0"):
        return Decimal("0")
    return normalized / MAX_EXECUTION_COST_BPS
