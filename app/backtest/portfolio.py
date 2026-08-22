from dataclasses import dataclass

import pandas as pd

from app.backtest.tax_accounting import TaxAccountingPolicy


@dataclass(frozen=True)
class ExecutionAssumptions:
    """Explicit assumptions for a cash-only, long-only virtual account."""

    fee_rate: float = 0.001
    spread_rate: float = 0.001
    tax_rate: float = 0.0
    lot_size: int = 100
    maximum_positions: int = 2
    maximum_position_rate: float = 0.30

    def __post_init__(self) -> None:
        rates = (self.fee_rate, self.spread_rate, self.tax_rate)
        if any(rate < 0 for rate in rates):
            raise ValueError("fee_rate, spread_rate, and tax_rate must be non-negative")
        if self.lot_size <= 0 or self.maximum_positions <= 0:
            raise ValueError("lot_size and maximum_positions must be positive")
        if not 0 < self.maximum_position_rate <= 1:
            raise ValueError("maximum_position_rate must be between 0 and 1")
        if self.tax_rate != 0:
            raise ValueError(
                "tax_rate must remain 0; virtual-account results are pretax and do not model taxation"
            )


def _performance_metrics(
    snapshots: pd.DataFrame,
    ledger: pd.DataFrame,
    initial_cash: float,
    benchmark: pd.Series | None,
) -> dict:
    if snapshots.empty:
        return {
            "total_return": 0.0,
            "maximum_drawdown": 0.0,
            "closed_trades": 0,
            "win_rate": None,
            "average_trade_return": None,
            "benchmark_return": None,
            "excess_return": None,
        }

    total_return = float(snapshots.iloc[-1]["equity"] / initial_cash - 1)
    closed = ledger[ledger["action"] == "exit"] if not ledger.empty else pd.DataFrame()
    win_rate = float((closed["realized_pnl"] > 0).mean()) if not closed.empty else None
    average_trade_return = float(closed["trade_return"].mean()) if not closed.empty else None
    benchmark_return = None
    if benchmark is not None:
        usable = benchmark.copy()
        if isinstance(usable.index, pd.DatetimeIndex):
            benchmark_index = pd.to_datetime(usable.index, utc=True).normalize()
            usable.index = benchmark_index
            start = pd.to_datetime(snapshots["date"], utc=True).min()
            end = pd.to_datetime(snapshots["date"], utc=True).max()
            usable = usable.loc[start:end]
        usable = pd.to_numeric(usable, errors="coerce").dropna()
        if len(usable) >= 2 and float(usable.iloc[0]) > 0:
            benchmark_return = float(usable.iloc[-1] / usable.iloc[0] - 1)
    return {
        "total_return": total_return,
        "maximum_drawdown": float(snapshots["drawdown"].min()),
        "closed_trades": int(len(closed)),
        "win_rate": win_rate,
        "average_trade_return": average_trade_return,
        "benchmark_return": benchmark_return,
        "excess_return": None if benchmark_return is None else total_return - benchmark_return,
    }


def simulate_long_portfolio(
    trades: pd.DataFrame,
    *,
    initial_cash: float = 2_500_000,
    account_name: str = "short_term",
    assumptions: ExecutionAssumptions | None = None,
    price_history: pd.DataFrame | None = None,
    benchmark: pd.Series | None = None,
    tax_accounting_policy: TaxAccountingPolicy | None = None,
) -> dict:
    """Run an event-ordered, cash-reserving, long-only virtual portfolio.

    ``trades`` must contain a decision date, a later entry date, and explicit
    entry/exit prices. Entries consume cash immediately. Positions closing on
    an entry date are closed after that day's new entries, so close proceeds
    cannot fund an earlier opening-auction trade.
    """

    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    assumptions = assumptions or ExecutionAssumptions()
    tax_accounting_policy = tax_accounting_policy or TaxAccountingPolicy()
    tax_disclosure = tax_accounting_policy.disclosure()
    tax_metadata = {
        "tax_accounting_version": tax_accounting_policy.version,
        "tax_evaluation_basis": tax_accounting_policy.evaluation_basis,
    }
    required = {
        "signal_date",
        "entry_date",
        "exit_date",
        "symbol",
        "entry_price",
        "exit_price",
    }
    if trades.empty:
        empty = pd.DataFrame()
        metrics = _performance_metrics(empty, empty, initial_cash, benchmark)
        return {
            "account_name": account_name,
            "initial_cash": float(initial_cash),
            "cash": float(initial_cash),
            "equity": float(initial_cash),
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "positions": empty,
            "trades": empty,
            "snapshots": empty,
            "rejected_trades": empty,
            "metrics": metrics,
            "assumptions": assumptions,
            "tax_accounting_policy": tax_accounting_policy,
            "tax_summary": tax_disclosure,
            "scope": "long_only_cash_account",
            "execution_warning": "signal_dateより後のentry_dateだけを許可し、空売りは実行しません。",
        }
    missing = required.difference(trades.columns)
    if missing:
        raise ValueError(f"trades missing required columns: {sorted(missing)}")

    ordered = trades.copy().reset_index(drop=True)
    ordered["trade_id"] = ordered.index.astype(int)
    for column in ("signal_date", "entry_date", "exit_date"):
        ordered[column] = pd.to_datetime(ordered[column], utc=True).dt.normalize()
    ordered["entry_price"] = pd.to_numeric(ordered["entry_price"], errors="coerce")
    ordered["exit_price"] = pd.to_numeric(ordered["exit_price"], errors="coerce")
    if "score" not in ordered:
        ordered["score"] = 0.0
    ordered = ordered.sort_values(["entry_date", "signal_date", "score"], ascending=[True, True, False])

    history = pd.DataFrame()
    price_table = pd.DataFrame()
    if price_history is not None and not price_history.empty:
        history = price_history.copy()
        history["price_time"] = pd.to_datetime(history["price_time"], utc=True).dt.normalize()
        history["close"] = pd.to_numeric(history["close"], errors="coerce")
        price_table = history.pivot_table(
            index="price_time", columns="symbol", values="close", aggfunc="last"
        ).sort_index()

    event_dates = pd.DatetimeIndex(
        sorted(set(ordered["entry_date"].tolist()) | set(ordered["exit_date"].tolist()))
    )
    if not price_table.empty:
        first_event = event_dates.min()
        last_event = event_dates.max()
        valuation_dates = price_table.loc[first_event:last_event].index
        event_dates = event_dates.union(valuation_dates).sort_values()

    cash = float(initial_cash)
    realized_pnl = 0.0
    positions: dict[str, dict] = {}
    ledger: list[dict] = []
    rejected: list[dict] = []
    snapshots: list[dict] = []
    high_watermark = float(initial_cash)

    def reject(row: dict, reason: str) -> None:
        rejected.append(
            {
                "trade_id": row["trade_id"],
                "symbol": row["symbol"],
                "signal_date": row["signal_date"],
                "entry_date": row["entry_date"],
                "reason": reason,
            }
        )

    for current_date in event_dates:
        entries = ordered[ordered["entry_date"] == current_date]
        for row in entries.to_dict(orient="records"):
            symbol = str(row["symbol"])
            side = str(row.get("side", "long"))
            if side != "long":
                reject(row, "long_only_account")
                continue
            if row["signal_date"] >= row["entry_date"]:
                reject(row, "entry_not_after_signal")
                continue
            if row["exit_date"] < row["entry_date"]:
                reject(row, "exit_before_entry")
                continue
            if pd.isna(row["entry_price"]) or pd.isna(row["exit_price"]):
                reject(row, "missing_execution_price")
                continue
            if float(row["entry_price"]) <= 0 or float(row["exit_price"]) <= 0:
                reject(row, "invalid_execution_price")
                continue
            if symbol in positions:
                reject(row, "symbol_already_held")
                continue
            if len(positions) >= assumptions.maximum_positions:
                reject(row, "maximum_positions_reached")
                continue

            execution_price = float(row["entry_price"]) * (1 + assumptions.spread_rate / 2)
            budget = min(cash, initial_cash * assumptions.maximum_position_rate)
            unit_cost = execution_price * assumptions.lot_size * (1 + assumptions.fee_rate)
            quantity = int(budget // unit_cost) * assumptions.lot_size
            if quantity <= 0:
                reject(row, "insufficient_cash_for_lot")
                continue
            gross = execution_price * quantity
            entry_fee = gross * assumptions.fee_rate
            cost = gross + entry_fee
            if cost > cash:
                reject(row, "insufficient_cash")
                continue
            cash -= cost
            positions[symbol] = {
                **row,
                "quantity": quantity,
                "entry_execution_price": execution_price,
                "entry_fee": entry_fee,
                "cost": cost,
            }
            ledger.append(
                {
                    "account_name": account_name,
                    "trade_id": row["trade_id"],
                    "date": current_date,
                    "action": "entry",
                    "symbol": symbol,
                    "quantity": quantity,
                    "execution_price": execution_price,
                    "fee": entry_fee,
                    "tax": 0.0,
                    **tax_metadata,
                    "cash_after": cash,
                    "realized_pnl": 0.0,
                    "trade_return": 0.0,
                    "decision_as_of": row["signal_date"],
                }
            )

        for symbol, position in list(positions.items()):
            if position["exit_date"] != current_date:
                continue
            execution_price = float(position["exit_price"]) * (1 - assumptions.spread_rate / 2)
            gross = execution_price * position["quantity"]
            exit_fee = gross * assumptions.fee_rate
            pre_tax_pnl = gross - exit_fee - position["cost"]
            tax = max(pre_tax_pnl, 0.0) * tax_accounting_policy.capital_gains_tax_rate
            proceeds = gross - exit_fee - tax
            pnl = proceeds - position["cost"]
            cash += proceeds
            realized_pnl += pnl
            ledger.append(
                {
                    "account_name": account_name,
                    "trade_id": position["trade_id"],
                    "date": current_date,
                    "action": "exit",
                    "symbol": symbol,
                    "quantity": position["quantity"],
                    "execution_price": execution_price,
                    "fee": exit_fee,
                    "tax": tax,
                    **tax_metadata,
                    "cash_after": cash,
                    "realized_pnl": pnl,
                    "trade_return": pnl / position["cost"],
                    "decision_as_of": position["signal_date"],
                }
            )
            del positions[symbol]

        market_value = 0.0
        for symbol, position in positions.items():
            valuation_price = position["entry_execution_price"]
            if not price_table.empty and symbol in price_table:
                available = price_table.loc[:current_date, symbol].dropna()
                if not available.empty:
                    valuation_price = float(available.iloc[-1])
            market_value += valuation_price * position["quantity"]
        equity = cash + market_value
        high_watermark = max(high_watermark, equity)
        snapshots.append(
            {
                "date": current_date,
                "cash": cash,
                "market_value": market_value,
                "equity": equity,
                "realized_pnl": realized_pnl,
                "unrealized_pnl": equity - cash - sum(
                    position["cost"] for position in positions.values()
                ),
                "drawdown": equity / high_watermark - 1,
            }
        )

    ledger_frame = pd.DataFrame(ledger)
    snapshot_frame = pd.DataFrame(snapshots)
    positions_frame = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "quantity": position["quantity"],
                "entry_date": position["entry_date"],
                "entry_price": position["entry_execution_price"],
                "cost": position["cost"],
            }
            for symbol, position in positions.items()
        ]
    )
    metrics = _performance_metrics(snapshot_frame, ledger_frame, initial_cash, benchmark)
    latest_equity = float(snapshot_frame.iloc[-1]["equity"])
    latest_unrealized = float(snapshot_frame.iloc[-1]["unrealized_pnl"])
    return {
        "account_name": account_name,
        "initial_cash": float(initial_cash),
        "cash": cash,
        "equity": latest_equity,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": latest_unrealized,
        "positions": positions_frame,
        "trades": ledger_frame,
        "snapshots": snapshot_frame,
        "rejected_trades": pd.DataFrame(rejected),
        "metrics": metrics,
        "assumptions": assumptions,
        "tax_accounting_policy": tax_accounting_policy,
        "tax_summary": tax_disclosure,
        "scope": "long_only_cash_account",
        "execution_warning": "signal_dateより後のentry_dateだけを許可し、空売りは実行しません。",
    }
