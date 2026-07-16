import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from src.domain.enums import PositionSide
from src.domain.models import ProfitBucket
from src.domain.enums import StrategyPhase
from src.portfolio.state import HedgeState


class JsonlStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict) -> None:
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


class SqliteStateStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hedge_state (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                underlying_symbol TEXT,
                symbol TEXT,
                long_symbol TEXT,
                short_symbol TEXT,
                phase TEXT NOT NULL,
                long_notional TEXT NOT NULL,
                short_notional TEXT NOT NULL,
                long_filled INTEGER NOT NULL,
                short_filled INTEGER NOT NULL,
                sim_leverage TEXT NOT NULL DEFAULT '20',
                sim_long_qty TEXT NOT NULL DEFAULT '0',
                sim_short_qty TEXT NOT NULL DEFAULT '0',
                sim_long_entry_price TEXT NOT NULL DEFAULT '0',
                sim_short_entry_price TEXT NOT NULL DEFAULT '0',
                sim_long_unrealized_pnl TEXT NOT NULL DEFAULT '0',
                sim_short_unrealized_pnl TEXT NOT NULL DEFAULT '0',
                sim_last_mark_price TEXT NOT NULL DEFAULT '0',
                sim_take_profit_count INTEGER NOT NULL DEFAULT 0,
                sim_restore_count INTEGER NOT NULL DEFAULT 0,
                sim_cycle_id INTEGER NOT NULL DEFAULT 0,
                last_symbol_switch_minute INTEGER
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_state (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                pending_rebalance_side TEXT,
                realized_pnl_total TEXT NOT NULL DEFAULT '0',
                realized_pnl_available_for_deposit TEXT NOT NULL DEFAULT '0',
                harvest_buffer TEXT NOT NULL DEFAULT '0',
                rwusd_principal TEXT NOT NULL DEFAULT '0',
                rwusd_interest_accrued TEXT NOT NULL DEFAULT '0',
                rwusd_redeemable TEXT NOT NULL DEFAULT '0',
                harvest_count INTEGER NOT NULL DEFAULT 0,
                deposit_count INTEGER NOT NULL DEFAULT 0,
                redeem_count INTEGER NOT NULL DEFAULT 0,
                closed_loop_ready INTEGER NOT NULL DEFAULT 0,
                last_rebalance_action TEXT,
                sweep_block_reason TEXT
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS dry_run_order_lifecycle (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                payload TEXT NOT NULL
            )
            """
        )
        self._ensure_columns()
        self._ensure_runtime_state_columns()
        self._connection.commit()

    def save_hedge_state(self, state: HedgeState) -> None:
        self._connection.execute(
            """
            INSERT INTO hedge_state (
                singleton_id,
                underlying_symbol,
                symbol,
                long_symbol,
                short_symbol,
                phase,
                long_notional,
                short_notional,
                long_filled,
                short_filled,
                sim_leverage,
                sim_long_qty,
                sim_short_qty,
                sim_long_entry_price,
                sim_short_entry_price,
                sim_long_unrealized_pnl,
                sim_short_unrealized_pnl,
                sim_last_mark_price,
                sim_take_profit_count,
                sim_restore_count,
                sim_cycle_id,
                last_symbol_switch_minute
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(singleton_id) DO UPDATE SET
                underlying_symbol = excluded.underlying_symbol,
                symbol = excluded.symbol,
                long_symbol = excluded.long_symbol,
                short_symbol = excluded.short_symbol,
                phase = excluded.phase,
                long_notional = excluded.long_notional,
                short_notional = excluded.short_notional,
                long_filled = excluded.long_filled,
                short_filled = excluded.short_filled,
                sim_leverage = excluded.sim_leverage,
                sim_long_qty = excluded.sim_long_qty,
                sim_short_qty = excluded.sim_short_qty,
                sim_long_entry_price = excluded.sim_long_entry_price,
                sim_short_entry_price = excluded.sim_short_entry_price,
                sim_long_unrealized_pnl = excluded.sim_long_unrealized_pnl,
                sim_short_unrealized_pnl = excluded.sim_short_unrealized_pnl,
                sim_last_mark_price = excluded.sim_last_mark_price,
                sim_take_profit_count = excluded.sim_take_profit_count,
                sim_restore_count = excluded.sim_restore_count,
                sim_cycle_id = excluded.sim_cycle_id,
                last_symbol_switch_minute = excluded.last_symbol_switch_minute
            """,
            (
                state.underlying_symbol,
                state.symbol,
                state.long_symbol,
                state.short_symbol,
                state.phase.value,
                str(state.long_notional),
                str(state.short_notional),
                1 if state.long_filled else 0,
                1 if state.short_filled else 0,
                str(state.sim_leverage),
                str(state.sim_long_qty),
                str(state.sim_short_qty),
                str(state.sim_long_entry_price),
                str(state.sim_short_entry_price),
                str(state.sim_long_unrealized_pnl),
                str(state.sim_short_unrealized_pnl),
                str(state.sim_last_mark_price),
                state.sim_take_profit_count,
                state.sim_restore_count,
                state.sim_cycle_id,
                state.last_symbol_switch_minute,
            ),
        )
        self._connection.commit()

    def load_hedge_state(self) -> HedgeState | None:
        row = self._connection.execute(
            """
            SELECT
                underlying_symbol,
                symbol,
                long_symbol,
                short_symbol,
                phase,
                long_notional,
                short_notional,
                long_filled,
                short_filled,
                sim_leverage,
                sim_long_qty,
                sim_short_qty,
                sim_long_entry_price,
                sim_short_entry_price,
                sim_long_unrealized_pnl,
                sim_short_unrealized_pnl,
                sim_last_mark_price,
                sim_take_profit_count,
                sim_restore_count,
                sim_cycle_id,
                last_symbol_switch_minute
            FROM hedge_state
            WHERE singleton_id = 1
            """
        ).fetchone()
        if row is None:
            return None

        return HedgeState(
            underlying_symbol=row[0],
            symbol=row[1],
            long_symbol=row[2],
            short_symbol=row[3],
            phase=StrategyPhase(row[4]),
            long_notional=Decimal(row[5]),
            short_notional=Decimal(row[6]),
            long_filled=bool(row[7]),
            short_filled=bool(row[8]),
            sim_leverage=Decimal(row[9]),
            sim_long_qty=Decimal(row[10]),
            sim_short_qty=Decimal(row[11]),
            sim_long_entry_price=Decimal(row[12]),
            sim_short_entry_price=Decimal(row[13]),
            sim_long_unrealized_pnl=Decimal(row[14]),
            sim_short_unrealized_pnl=Decimal(row[15]),
            sim_last_mark_price=Decimal(row[16]),
            sim_take_profit_count=int(row[17]),
            sim_restore_count=int(row[18]),
            sim_cycle_id=int(row[19]),
            last_symbol_switch_minute=None if row[20] is None else int(row[20]),
        )

    def save_runtime_state(
        self,
        *,
        profit_bucket: ProfitBucket,
        pending_rebalance_side: PositionSide | None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO runtime_state (
                singleton_id,
                pending_rebalance_side,
                realized_pnl_total,
                realized_pnl_available_for_deposit,
                harvest_buffer,
                rwusd_principal,
                rwusd_interest_accrued,
                rwusd_redeemable,
                harvest_count,
                deposit_count,
                redeem_count,
                closed_loop_ready,
                last_rebalance_action,
                sweep_block_reason
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(singleton_id) DO UPDATE SET
                pending_rebalance_side = excluded.pending_rebalance_side,
                realized_pnl_total = excluded.realized_pnl_total,
                realized_pnl_available_for_deposit = excluded.realized_pnl_available_for_deposit,
                harvest_buffer = excluded.harvest_buffer,
                rwusd_principal = excluded.rwusd_principal,
                rwusd_interest_accrued = excluded.rwusd_interest_accrued,
                rwusd_redeemable = excluded.rwusd_redeemable,
                harvest_count = excluded.harvest_count,
                deposit_count = excluded.deposit_count,
                redeem_count = excluded.redeem_count,
                closed_loop_ready = excluded.closed_loop_ready,
                last_rebalance_action = excluded.last_rebalance_action,
                sweep_block_reason = excluded.sweep_block_reason
            """,
            (
                None if pending_rebalance_side is None else pending_rebalance_side.value,
                str(profit_bucket.realized_pnl_total),
                str(profit_bucket.realized_pnl_available_for_deposit),
                str(profit_bucket.harvest_buffer),
                str(profit_bucket.rwusd_principal),
                str(profit_bucket.rwusd_interest_accrued),
                str(profit_bucket.rwusd_redeemable),
                int(profit_bucket.harvest_count),
                int(profit_bucket.deposit_count),
                int(profit_bucket.redeem_count),
                1 if profit_bucket.closed_loop_ready else 0,
                profit_bucket.last_rebalance_action,
                profit_bucket.sweep_block_reason,
            ),
        )
        self._connection.commit()

    def load_runtime_state(self) -> dict[str, object] | None:
        row = self._connection.execute(
            """
            SELECT
                pending_rebalance_side,
                realized_pnl_total,
                realized_pnl_available_for_deposit,
                harvest_buffer,
                rwusd_principal,
                rwusd_interest_accrued,
                rwusd_redeemable,
                harvest_count,
                deposit_count,
                redeem_count,
                closed_loop_ready,
                last_rebalance_action,
                sweep_block_reason
            FROM runtime_state
            WHERE singleton_id = 1
            """
        ).fetchone()
        if row is None:
            return None

        pending_side = (
            PositionSide(row[0])
            if row[0] not in (None, "")
            else None
        )
        return {
            "pending_rebalance_side": pending_side,
            "profit_bucket": ProfitBucket(
                realized_pnl_total=Decimal(row[1]),
                realized_pnl_available_for_deposit=Decimal(row[2]),
                harvest_buffer=Decimal(row[3]),
                rwusd_principal=Decimal(row[4]),
                rwusd_interest_accrued=Decimal(row[5]),
                rwusd_redeemable=Decimal(row[6]),
                harvest_count=int(row[7]),
                deposit_count=int(row[8]),
                redeem_count=int(row[9]),
                closed_loop_ready=bool(row[10]),
                last_rebalance_action=row[11],
                sweep_block_reason=row[12],
            ),
        }

    def save_dry_run_order_lifecycle(self, snapshot: dict[str, object]) -> None:
        self._connection.execute(
            """
            INSERT INTO dry_run_order_lifecycle (singleton_id, payload)
            VALUES (1, ?)
            ON CONFLICT(singleton_id) DO UPDATE SET payload = excluded.payload
            """,
            (json.dumps(snapshot, ensure_ascii=True),),
        )
        self._connection.commit()

    def load_dry_run_order_lifecycle(self) -> dict[str, object] | None:
        row = self._connection.execute(
            """
            SELECT payload
            FROM dry_run_order_lifecycle
            WHERE singleton_id = 1
            """
        ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def close(self) -> None:
        self._connection.close()

    def _ensure_columns(self) -> None:
        existing = {
            row[1]
            for row in self._connection.execute("PRAGMA table_info(hedge_state)")
        }
        if "underlying_symbol" not in existing:
            self._connection.execute(
                "ALTER TABLE hedge_state ADD COLUMN underlying_symbol TEXT"
            )
        if "long_symbol" not in existing:
            self._connection.execute(
                "ALTER TABLE hedge_state ADD COLUMN long_symbol TEXT"
            )
        if "short_symbol" not in existing:
            self._connection.execute(
                "ALTER TABLE hedge_state ADD COLUMN short_symbol TEXT"
            )
        if "sim_leverage" not in existing:
            self._connection.execute(
                "ALTER TABLE hedge_state ADD COLUMN sim_leverage TEXT NOT NULL DEFAULT '20'"
            )
        if "sim_long_qty" not in existing:
            self._connection.execute(
                "ALTER TABLE hedge_state ADD COLUMN sim_long_qty TEXT NOT NULL DEFAULT '0'"
            )
        if "sim_short_qty" not in existing:
            self._connection.execute(
                "ALTER TABLE hedge_state ADD COLUMN sim_short_qty TEXT NOT NULL DEFAULT '0'"
            )
        if "sim_long_entry_price" not in existing:
            self._connection.execute(
                "ALTER TABLE hedge_state ADD COLUMN sim_long_entry_price TEXT NOT NULL DEFAULT '0'"
            )
        if "sim_short_entry_price" not in existing:
            self._connection.execute(
                "ALTER TABLE hedge_state ADD COLUMN sim_short_entry_price TEXT NOT NULL DEFAULT '0'"
            )
        if "sim_long_unrealized_pnl" not in existing:
            self._connection.execute(
                "ALTER TABLE hedge_state ADD COLUMN sim_long_unrealized_pnl TEXT NOT NULL DEFAULT '0'"
            )
        if "sim_short_unrealized_pnl" not in existing:
            self._connection.execute(
                "ALTER TABLE hedge_state ADD COLUMN sim_short_unrealized_pnl TEXT NOT NULL DEFAULT '0'"
            )
        if "sim_last_mark_price" not in existing:
            self._connection.execute(
                "ALTER TABLE hedge_state ADD COLUMN sim_last_mark_price TEXT NOT NULL DEFAULT '0'"
            )
        if "sim_take_profit_count" not in existing:
            self._connection.execute(
                "ALTER TABLE hedge_state ADD COLUMN sim_take_profit_count INTEGER NOT NULL DEFAULT 0"
            )
        if "sim_restore_count" not in existing:
            self._connection.execute(
                "ALTER TABLE hedge_state ADD COLUMN sim_restore_count INTEGER NOT NULL DEFAULT 0"
            )
        if "sim_cycle_id" not in existing:
            self._connection.execute(
                "ALTER TABLE hedge_state ADD COLUMN sim_cycle_id INTEGER NOT NULL DEFAULT 0"
            )
        if "last_symbol_switch_minute" not in existing:
            self._connection.execute(
                "ALTER TABLE hedge_state ADD COLUMN last_symbol_switch_minute INTEGER"
            )

    def _ensure_runtime_state_columns(self) -> None:
        existing = {
            row[1]
            for row in self._connection.execute("PRAGMA table_info(runtime_state)")
        }
        required_columns = {
            "pending_rebalance_side": "TEXT",
            "realized_pnl_total": "TEXT NOT NULL DEFAULT '0'",
            "realized_pnl_available_for_deposit": "TEXT NOT NULL DEFAULT '0'",
            "harvest_buffer": "TEXT NOT NULL DEFAULT '0'",
            "rwusd_principal": "TEXT NOT NULL DEFAULT '0'",
            "rwusd_interest_accrued": "TEXT NOT NULL DEFAULT '0'",
            "rwusd_redeemable": "TEXT NOT NULL DEFAULT '0'",
            "harvest_count": "INTEGER NOT NULL DEFAULT 0",
            "deposit_count": "INTEGER NOT NULL DEFAULT 0",
            "redeem_count": "INTEGER NOT NULL DEFAULT 0",
            "closed_loop_ready": "INTEGER NOT NULL DEFAULT 0",
            "last_rebalance_action": "TEXT",
            "sweep_block_reason": "TEXT",
        }
        for name, definition in required_columns.items():
            if name in existing:
                continue
            self._connection.execute(
                f"ALTER TABLE runtime_state ADD COLUMN {name} {definition}"
            )
