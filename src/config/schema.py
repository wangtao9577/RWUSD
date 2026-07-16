from decimal import Decimal

from pydantic import BaseModel, Field

from src.domain.models import DEFAULT_SIM_LEVERAGE


class ExchangeSettings(BaseModel):
    api_key: str
    api_secret: str
    base_url: str
    ws_base_url: str | None = None


class UniverseSettings(BaseModel):
    candidate_symbols: list[str] = Field(min_length=1)


class BacktestSettings(BaseModel):
    primary_bar_interval: str = Field(default="5m")


class PositioningSettings(BaseModel):
    target_notional: Decimal = Field(default=Decimal("1000"))
    sim_leverage: Decimal = Field(default=DEFAULT_SIM_LEVERAGE)


class TakeProfitSettings(BaseModel):
    long_take_profit: Decimal = Field(default=Decimal("25"))
    short_take_profit: Decimal = Field(default=Decimal("25"))


class RiskSettings(BaseModel):
    soft_unimmr: Decimal = Field(default=Decimal("6"))
    hard_unimmr: Decimal = Field(default=Decimal("4"))
    max_drawdown: Decimal = Field(default=Decimal("0.15"))
    redeem_unimmr: Decimal = Field(default=Decimal("0"))
    max_total_abs_leverage: Decimal | None = Field(default=None, ge=0)
    max_total_net_leverage: Decimal | None = Field(default=None, ge=0)
    max_single_symbol_net_leverage: Decimal | None = Field(default=None, ge=0)


class TransferSettings(BaseModel):
    min_sweep: Decimal = Field(default=Decimal("50"))
    pm_reserve: Decimal = Field(default=Decimal("100"))
    min_redeem: Decimal = Field(default=Decimal("50"))


class HarvestSettings(BaseModel):
    min_net_pnl: Decimal = Field(default=Decimal("18"))
    taker_fee_bps: Decimal = Field(default=Decimal("5"))
    slippage_bps: Decimal = Field(default=Decimal("5"))


class YieldSettings(BaseModel):
    rwusd_apr: Decimal = Field(default=Decimal("0.12"))


class SelectorRuntimeSettings(BaseModel):
    eval_interval_minutes: int = Field(default=15)
    switch_edge: Decimal = Field(default=Decimal("0.20"))


class UsdcMakerSettings(BaseModel):
    enabled: bool = Field(default=True)
    allowed_phases: list[str] = Field(
        default_factory=lambda: ["open_hedge", "restore_now", "recover_missing_leg"]
    )
    fallback_to_market_on_missing_price: bool = Field(default=True)


class DryRunExecutionSettings(BaseModel):
    fill_fraction: Decimal = Field(default=Decimal("1"), gt=0, le=1)
    min_fill_quantity: Decimal = Field(default=Decimal("0"), ge=0)
    order_timeout_cycles: int = Field(default=0, ge=0)
    max_requotes: int = Field(default=0, ge=0)


class LiveSettings(BaseModel):
    dry_run: bool = Field(default=True)
    log_path: str | None = None
    log_rotate_daily: bool = Field(default=False)
    bull_rebalance_delay_enabled: bool = Field(default=False)
    user_stream_retry_attempts: int = Field(default=0, ge=0)
    user_stream_retry_backoff_seconds: float = Field(default=1.0, ge=0.0)
    user_stream_retry_backoff_multiplier: float = Field(default=2.0, gt=0.0)
    cycle_retry_attempts: int = Field(default=0, ge=0)
    cycle_retry_backoff_seconds: float = Field(default=1.0, ge=0.0)
    cycle_retry_backoff_multiplier: float = Field(default=2.0, gt=0.0)


class Settings(BaseModel):
    exchange: ExchangeSettings
    universe: UniverseSettings
    backtest: BacktestSettings
    positioning: PositioningSettings
    take_profit: TakeProfitSettings
    risk: RiskSettings
    transfer: TransferSettings
    harvest: HarvestSettings
    yield_config: YieldSettings
    selector: SelectorRuntimeSettings
    usdc_maker: UsdcMakerSettings
    dry_run_execution: DryRunExecutionSettings
    live: LiveSettings
