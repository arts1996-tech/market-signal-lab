from dataclasses import dataclass
import math

import pandas as pd

from app.backtest.audit import build_run_manifest, decision_card
from app.backtest.portfolio import ExecutionAssumptions


@dataclass(frozen=True)
class MarketImpactAssumptions:
    require_volume: bool = False
    maximum_volume_participation: float = 0.10
    allow_partial_fill: bool = True
    base_slippage_rate: float = 0.0005
    impact_rate: float = 0.005
    minimum_previous_turnover: float = 0.0
    use_turnover_cost_model: bool = False
    high_turnover_threshold: float = 1_000_000_000.0
    medium_turnover_threshold: float = 250_000_000.0
    low_turnover_threshold: float = 50_000_000.0
    simultaneous_hit_policy: str = "stop_first"

    def __post_init__(self) -> None:
        if not 0 < self.maximum_volume_participation <= 1:
            raise ValueError("maximum_volume_participation must be between 0 and 1")
        if self.base_slippage_rate < 0 or self.impact_rate < 0:
            raise ValueError("slippage rates must be non-negative")
        if self.minimum_previous_turnover < 0:
            raise ValueError("minimum_previous_turnover must be non-negative")
        if not (
            self.high_turnover_threshold
            > self.medium_turnover_threshold
            > self.low_turnover_threshold
            > 0
        ):
            raise ValueError("turnover thresholds must be positive and descending")
        if self.simultaneous_hit_policy != "stop_first":
            raise ValueError("only conservative stop_first policy is supported")


@dataclass(frozen=True)
class PortfolioRiskRules:
    maximum_sector_rate: float = 0.50
    maximum_drawdown: float = 0.20
    maximum_risk_per_trade_rate: float = 0.01
    maximum_total_open_risk_rate: float = 0.05
    loss_streak_threshold: int = 3
    cooldown_sessions: int = 5
    maximum_position_correlation: float | None = None
    correlation_lookback_sessions: int = 60
    minimum_correlation_observations: int = 20

    def __post_init__(self) -> None:
        if not 0 < self.maximum_sector_rate <= 1:
            raise ValueError("maximum_sector_rate must be between 0 and 1")
        if not 0 < self.maximum_drawdown < 1:
            raise ValueError("maximum_drawdown must be between 0 and 1")
        if not 0 < self.maximum_risk_per_trade_rate < 1:
            raise ValueError("maximum_risk_per_trade_rate must be between 0 and 1")
        if not 0 < self.maximum_total_open_risk_rate < 1:
            raise ValueError("maximum_total_open_risk_rate must be between 0 and 1")
        if self.maximum_risk_per_trade_rate > self.maximum_total_open_risk_rate:
            raise ValueError("per-trade risk cannot exceed total open risk")
        if self.loss_streak_threshold <= 0 or self.cooldown_sessions < 0:
            raise ValueError("loss_streak_threshold must be positive and cooldown non-negative")
        if self.maximum_position_correlation is not None and not 0 < self.maximum_position_correlation <= 1:
            raise ValueError("maximum_position_correlation must be between 0 and 1")
        if self.correlation_lookback_sessions < 2:
            raise ValueError("correlation_lookback_sessions must be at least 2")
        if self.minimum_correlation_observations < 2:
            raise ValueError("minimum_correlation_observations must be at least 2")


def _empty_result(
    account_name: str,
    initial_cash: float,
    manifest: dict,
    assumptions: ExecutionAssumptions,
    market_impact: MarketImpactAssumptions,
    risk_rules: PortfolioRiskRules,
) -> dict:
    empty = pd.DataFrame()
    return {
        "account_name": account_name,
        "initial_cash": float(initial_cash),
        "cash": float(initial_cash),
        "equity": float(initial_cash),
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "positions": empty,
        "transactions": empty,
        "snapshots": empty,
        "rejected_signals": empty,
        "decision_cards": empty,
        "benchmark_comparisons": empty,
        "metrics": {
            "total_return": 0.0,
            "maximum_drawdown": 0.0,
            "closed_trades": 0,
            "win_rate": None,
            "average_trade_return": None,
            "win_rate_ci95": None,
            "average_trade_return_ci95": None,
            "benchmark_return": None,
            "excess_return": None,
        },
        "manifest": manifest,
        "assumptions": assumptions,
        "market_impact": market_impact,
        "risk_rules": risk_rules,
        "risk_halted": False,
    }


def _benchmark_return(benchmark: pd.Series | None, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    if benchmark is None:
        return None
    usable = benchmark.copy()
    usable.index = pd.to_datetime(usable.index, utc=True).normalize()
    usable = pd.to_numeric(usable.loc[start:end], errors="coerce").dropna()
    if len(usable) < 2 or float(usable.iloc[0]) <= 0:
        return None
    return float(usable.iloc[-1] / usable.iloc[0] - 1)


def _mean_confidence_interval_95(values: pd.Series) -> list[float] | None:
    usable = pd.to_numeric(values, errors="coerce").dropna()
    if len(usable) < 2:
        return None
    mean = float(usable.mean())
    margin = 1.96 * float(usable.std(ddof=1)) / math.sqrt(len(usable))
    return [mean - margin, mean + margin]


def _wilson_interval_95(wins: int, total: int) -> list[float] | None:
    if total <= 0:
        return None
    z = 1.96
    rate = wins / total
    denominator = 1 + z**2 / total
    centre = (rate + z**2 / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(rate * (1 - rate) / total + z**2 / (4 * total**2))
        / denominator
    )
    return [max(0.0, centre - margin), min(1.0, centre + margin)]


def _is_true(value) -> bool:
    return bool(value) if pd.notna(value) else False


def _last_close_or_entry(
    price_lookup: dict,
    symbol: str,
    session: pd.Timestamp,
    entry_price: float,
) -> float:
    value = price_lookup.get((symbol, session), {}).get("close")
    return entry_price if not _valid_positive_number(value) else float(value)


def _valid_positive_number(value) -> bool:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(parsed) and parsed > 0


def _valid_ohlc_bar(bar: dict) -> bool:
    if not all(_valid_positive_number(bar.get(column)) for column in ("open", "high", "low", "close")):
        return False
    open_price = float(bar["open"])
    high = float(bar["high"])
    low = float(bar["low"])
    close = float(bar["close"])
    return high >= max(open_price, close) and low <= min(open_price, close)


def _optional_rate(signal: dict, key: str) -> float | None:
    value = signal.get(key)
    if value is None or pd.isna(value):
        return None
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0 or parsed > 0.05:
        raise ValueError(f"{key} must be between 0 and 0.05")
    return parsed


def _execution_cost_profile(
    signal: dict,
    assumptions: ExecutionAssumptions,
    market_impact: MarketImpactAssumptions,
    previous_turnover: float | None,
) -> dict:
    explicit_spread = _optional_rate(signal, "spread_rate")
    explicit_slippage = _optional_rate(signal, "base_slippage_rate")
    explicit_impact = _optional_rate(signal, "impact_rate")
    if (
        explicit_spread is not None
        or explicit_slippage is not None
        or explicit_impact is not None
    ):
        return {
            "profile": "signal_specific_v1",
            "spread_rate": assumptions.spread_rate if explicit_spread is None else explicit_spread,
            "base_slippage_rate": (
                market_impact.base_slippage_rate
                if explicit_slippage is None
                else explicit_slippage
            ),
            "impact_rate": market_impact.impact_rate if explicit_impact is None else explicit_impact,
        }
    if not market_impact.use_turnover_cost_model:
        return {
            "profile": "fixed_cost_v1",
            "spread_rate": assumptions.spread_rate,
            "base_slippage_rate": market_impact.base_slippage_rate,
            "impact_rate": market_impact.impact_rate,
        }
    if previous_turnover is None or not _valid_positive_number(previous_turnover):
        raise ValueError("previous turnover is required for turnover cost model")
    if previous_turnover >= market_impact.high_turnover_threshold:
        spread_rate, base_slippage_rate, tier = 0.0005, 0.00025, "high"
    elif previous_turnover >= market_impact.medium_turnover_threshold:
        spread_rate, base_slippage_rate, tier = 0.0010, 0.00050, "medium"
    elif previous_turnover >= market_impact.low_turnover_threshold:
        spread_rate, base_slippage_rate, tier = 0.0020, 0.00100, "low"
    else:
        raise ValueError("previous turnover is below modeled liquidity tiers")
    return {
        "profile": f"turnover_cost_v1:{tier}",
        "spread_rate": spread_rate,
        "base_slippage_rate": base_slippage_rate,
        "impact_rate": market_impact.impact_rate,
    }


def simulate_ohlc_portfolio(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    *,
    initial_cash: float = 2_500_000,
    account_name: str = "short_term",
    assumptions: ExecutionAssumptions | None = None,
    market_impact: MarketImpactAssumptions | None = None,
    risk_rules: PortfolioRiskRules | None = None,
    benchmark: pd.Series | None = None,
    benchmarks: dict[str, pd.Series] | None = None,
    input_data_version: str | None = None,
    strategy_version: str | None = None,
    execution_version: str | None = None,
) -> dict:
    """Simulate long-only daily OHLC signals with conservative execution.

    Signals are decided on ``signal_date`` and may enter only on a later
    ``entry_date``. Entry sizing uses the previous session's volume so the
    entry day's completed volume never leaks into the decision.
    """

    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    assumptions = assumptions or ExecutionAssumptions()
    market_impact = market_impact or MarketImpactAssumptions()
    risk_rules = risk_rules or PortfolioRiskRules()
    required_signals = {"signal_date", "entry_date", "symbol", "side"}
    required_prices = {"price_time", "symbol", "open", "high", "low", "close"}
    if not required_signals.issubset(signals.columns) and not signals.empty:
        raise ValueError(f"signals missing columns: {sorted(required_signals - set(signals.columns))}")
    if not required_prices.issubset(prices.columns) and not prices.empty:
        raise ValueError(f"prices missing columns: {sorted(required_prices - set(prices.columns))}")

    signal_frame = signals.copy()
    price_frame = prices.copy()
    for frame, column in ((signal_frame, "signal_date"), (signal_frame, "entry_date"), (price_frame, "price_time")):
        if not frame.empty:
            frame[column] = pd.to_datetime(frame[column], utc=True).dt.normalize()
    for column in ("open", "high", "low", "close", "volume"):
        if column in price_frame:
            price_frame[column] = pd.to_numeric(price_frame[column], errors="coerce")
    manifest = build_run_manifest(
        signal_frame,
        price_frame,
        account_name=account_name,
        assumptions={"execution": assumptions, "market_impact": market_impact},
        risk_rules=risk_rules,
        input_data_version=input_data_version,
        **({"strategy_version": strategy_version} if strategy_version else {}),
        **({"execution_version": execution_version} if execution_version else {}),
    )
    if signal_frame.empty or price_frame.empty:
        return _empty_result(
            account_name, initial_cash, manifest, assumptions, market_impact, risk_rules
        )

    price_frame = price_frame.sort_values(["symbol", "price_time"]).drop_duplicates(
        ["symbol", "price_time"], keep="last"
    )
    sessions = pd.DatetimeIndex(sorted(price_frame["price_time"].unique()))
    price_lookup = {
        (row.symbol, row.price_time): row._asdict()
        for row in price_frame.itertuples(index=False)
    }
    previous_bar: dict[tuple[str, pd.Timestamp], dict | None] = {}
    for symbol, group in price_frame.groupby("symbol"):
        previous = None
        for row in group.itertuples(index=False):
            previous_bar[(symbol, row.price_time)] = previous
            previous = row._asdict()

    if "score" not in signal_frame:
        signal_frame["score"] = 0.0
    else:
        signal_frame["score"] = pd.to_numeric(signal_frame["score"], errors="coerce").fillna(0)
    signal_frame = signal_frame.sort_values(
        ["entry_date", "score", "symbol"], ascending=[True, False, True]
    ).reset_index(drop=True)

    cash = float(initial_cash)
    realized_pnl = 0.0
    positions: dict[str, dict] = {}
    transactions: list[dict] = []
    rejected: list[dict] = []
    cards: list[dict] = []
    snapshots: list[dict] = []
    high_watermark = float(initial_cash)
    loss_streak = 0
    cooldown_until_index = -1
    risk_halted = False

    def reject(signal: dict, reason: str, warning: str) -> None:
        rejected.append(
            {
                "symbol": signal.get("symbol"),
                "signal_date": signal.get("signal_date"),
                "entry_date": signal.get("entry_date"),
                "reason": reason,
            }
        )
        card = decision_card(
            signal,
            status="rejected",
            manifest=manifest,
            outcome_reason=reason,
            quality_warnings=[warning],
            event_at=signal.get("entry_date") or signal.get("signal_date"),
        )
        cards.append(card)

    for session_index, current_date in enumerate(sessions):
        entries = signal_frame[signal_frame["entry_date"] == current_date]
        for signal in entries.to_dict(orient="records"):
            symbol = str(signal["symbol"])
            bar = price_lookup.get((symbol, current_date))
            if signal["side"] != "long":
                reject(signal, "long_only_account", "下方向・方向未確定は空売りしません")
                continue
            if signal["signal_date"] >= signal["entry_date"]:
                reject(signal, "entry_not_after_signal", "判断後のセッションでのみ約定できます")
                continue
            if risk_halted:
                reject(signal, "portfolio_drawdown_halt", "口座ドローダウン上限に到達しました")
                continue
            if session_index <= cooldown_until_index:
                reject(signal, "loss_streak_cooldown", "連続損失後のクールダウン中です")
                continue
            if bar is None or not _valid_positive_number(bar.get("open")):
                reject(signal, "missing_entry_open", "次営業日の始値がありません")
                continue
            if ("tradable" in bar and not _is_true(bar.get("tradable"))) or _is_true(
                bar.get("suspended")
            ):
                reject(signal, "not_tradable", "取引停止または取引不能です")
                continue
            if _is_true(bar.get("limit_up")):
                reject(signal, "limit_up_no_fill", "ストップ高で買い約定不能として扱います")
                continue
            if _is_true(bar.get("special_quote")):
                reject(signal, "special_quote_no_fill", "特別気配中は買い約定不能として扱います")
                continue
            if symbol in positions:
                reject(signal, "symbol_already_held", "同一銘柄の重複保有はしません")
                continue
            if len(positions) >= assumptions.maximum_positions:
                reject(signal, "maximum_positions_reached", "同時保有上限に到達しています")
                continue
            if risk_rules.maximum_position_correlation is not None and positions:
                history = price_frame[
                    (price_frame["price_time"] <= signal["signal_date"])
                    & (price_frame["symbol"].isin([symbol, *positions.keys()]))
                ].pivot(index="price_time", columns="symbol", values="close")
                returns = history.tail(
                    risk_rules.correlation_lookback_sessions + 1
                ).pct_change(fill_method=None)
                excessive_pair = None
                for held_symbol in positions:
                    pair = returns[[symbol, held_symbol]].dropna()
                    if len(pair) < risk_rules.minimum_correlation_observations:
                        continue
                    correlation = float(pair[symbol].corr(pair[held_symbol]))
                    if math.isfinite(correlation) and correlation >= risk_rules.maximum_position_correlation:
                        excessive_pair = (held_symbol, correlation)
                        break
                if excessive_pair is not None:
                    reject(
                        signal,
                        "position_correlation_limit",
                        f"保有中の{excessive_pair[0]}との相関{excessive_pair[1]:.2f}が集中上限以上です",
                    )
                    continue

            previous = previous_bar.get((symbol, current_date))
            previous_volume = None if previous is None else previous.get("volume")
            previous_close = None if previous is None else previous.get("close")
            previous_turnover = (
                float(previous_volume) * float(previous_close)
                if _valid_positive_number(previous_volume)
                and _valid_positive_number(previous_close)
                else None
            )
            if market_impact.require_volume and (
                previous_volume is None
                or not _valid_positive_number(previous_volume)
            ):
                reject(signal, "missing_previous_volume", "未来の出来高を使わず、前営業日の出来高が必要です")
                continue
            if market_impact.minimum_previous_turnover > 0:
                if (
                    previous_volume is None
                    or previous_close is None
                    or not _valid_positive_number(previous_volume)
                    or not _valid_positive_number(previous_close)
                ):
                    reject(signal, "missing_previous_turnover", "前営業日の売買代金を確認できません")
                    continue
                if previous_turnover < market_impact.minimum_previous_turnover:
                    reject(signal, "minimum_turnover_not_met", "前営業日の売買代金が基準未満です")
                    continue

            try:
                cost_profile = _execution_cost_profile(
                    signal,
                    assumptions,
                    market_impact,
                    previous_turnover,
                )
            except ValueError as exc:
                reject(signal, "execution_cost_profile_unavailable", str(exc))
                continue

            sector = str(signal.get("sector") or "unknown")
            stop_loss = float(signal.get("stop_loss", -0.05))
            take_profit = float(signal.get("take_profit", 0.08))
            maximum_holding_days = int(signal.get("maximum_holding_days", 10))
            if stop_loss >= 0 or take_profit <= 0 or maximum_holding_days <= 0:
                reject(signal, "invalid_exit_rules", "損切り・利益確定・保有期限の設定が不正です")
                continue
            sector_cost = sum(
                position["cost"]
                for position in positions.values()
                if position["sector"] == sector
            )
            sector_capacity = initial_cash * risk_rules.maximum_sector_rate - sector_cost
            existing_open_risk = sum(
                position["planned_risk"] for position in positions.values()
            )
            remaining_open_risk = max(
                0.0,
                initial_cash * risk_rules.maximum_total_open_risk_rate
                - existing_open_risk,
            )
            allowed_trade_risk = min(
                initial_cash * risk_rules.maximum_risk_per_trade_rate,
                remaining_open_risk,
            )
            risk_sized_budget = allowed_trade_risk / abs(stop_loss)
            budget = min(
                cash,
                initial_cash * assumptions.maximum_position_rate,
                max(0.0, sector_capacity),
                risk_sized_budget,
            )
            open_price = float(bar["open"])
            desired_quantity = int(
                budget
                // (
                    open_price
                    * assumptions.lot_size
                    * (1 + assumptions.fee_rate + cost_profile["spread_rate"] / 2)
                )
            ) * assumptions.lot_size
            quantity = desired_quantity
            participation = 0.0
            if _valid_positive_number(previous_volume):
                maximum_quantity = int(
                    float(previous_volume)
                    * market_impact.maximum_volume_participation
                    // assumptions.lot_size
                ) * assumptions.lot_size
                if desired_quantity > maximum_quantity:
                    if not market_impact.allow_partial_fill:
                        reject(signal, "liquidity_limit", "前営業日出来高に対して注文量が大きすぎます")
                        continue
                    quantity = maximum_quantity
                participation = quantity / float(previous_volume)
            if quantity <= 0:
                reject(
                    signal,
                    "insufficient_cash_liquidity_or_risk_budget",
                    "売買単位を満たす資金、流動性、または許容損失枠がありません",
                )
                continue

            slippage = (
                cost_profile["base_slippage_rate"]
                + cost_profile["impact_rate"] * participation
            )
            execution_price = open_price * (
                1 + cost_profile["spread_rate"] / 2 + slippage
            )
            gross = execution_price * quantity
            fee = gross * assumptions.fee_rate
            cost = gross + fee
            if cost > cash:
                reject(signal, "insufficient_cash", "費用込み必要額が現金を超えました")
                continue
            cash -= cost
            planned_risk = execution_price * abs(stop_loss) * quantity + fee
            position = {
                **signal,
                "quantity": quantity,
                "entry_execution_price": execution_price,
                "entry_fee": fee,
                "cost": cost,
                "sector": sector,
                "stop_price": execution_price * (1 + stop_loss),
                "take_profit_price": execution_price * (1 + take_profit),
                "maximum_holding_days": maximum_holding_days,
                "held_sessions": 0,
                "slippage_rate": slippage,
                "planned_risk": planned_risk,
                "reference_quantity": quantity,
                "spread_rate": cost_profile["spread_rate"],
                "execution_cost_profile": cost_profile["profile"],
                "previous_turnover": previous_turnover,
            }
            positions[symbol] = position
            transactions.append(
                {
                    "account": account_name,
                    "date": current_date,
                    "action": "仮想エントリー",
                    "symbol": symbol,
                    "name": signal.get("name", symbol),
                    "quantity": quantity,
                    "execution_price": execution_price,
                    "amount": cost,
                    "fee": fee,
                    "tax": 0.0,
                    "realized_pnl": 0.0,
                    "reason": " / ".join(signal.get("reasons", [])),
                    "decision_as_of": signal["signal_date"],
                    "participation_rate": participation,
                    "slippage_rate": slippage,
                    "spread_rate": cost_profile["spread_rate"],
                    "execution_cost_profile": cost_profile["profile"],
                    "previous_turnover": previous_turnover,
                }
            )
            card = decision_card(
                {
                    **signal,
                    "reference_quantity": quantity,
                    "planned_risk": planned_risk,
                },
                status="entered",
                manifest=manifest,
                entry_price=execution_price,
                event_at=current_date,
            )
            cards.append(card)

        for symbol, position in list(positions.items()):
            bar = price_lookup.get((symbol, current_date))
            if bar is None or not _valid_ohlc_bar(bar):
                card = decision_card(
                    position,
                    status="valuation_deferred",
                    manifest=manifest,
                    entry_price=position["entry_execution_price"],
                    outcome_reason="OHLC品質不合格",
                    quality_warnings=["OHLCの欠損・非正値・大小関係異常により決済判定を見送りました"],
                    event_at=current_date,
                )
                cards.append(card)
                continue
            if ("tradable" in bar and not _is_true(bar.get("tradable"))) or _is_true(
                bar.get("suspended")
            ):
                continue
            if _is_true(bar.get("limit_down")):
                card = decision_card(
                    position,
                    status="exit_deferred",
                    manifest=manifest,
                    entry_price=position["entry_execution_price"],
                    outcome_reason="値幅制限により売却不能",
                    quality_warnings=["ストップ安では売却できない保守的な仮定です"],
                    event_at=current_date,
                )
                cards.append(card)
                continue
            if _is_true(bar.get("special_quote")):
                card = decision_card(
                    position,
                    status="exit_deferred",
                    manifest=manifest,
                    entry_price=position["entry_execution_price"],
                    outcome_reason="特別気配により売却不能",
                    quality_warnings=["特別気配中は決済できない保守的な仮定です"],
                    event_at=current_date,
                )
                cards.append(card)
                continue
            position["held_sessions"] += 1
            open_price = float(bar["open"])
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
            stop_price = position["stop_price"]
            take_price = position["take_profit_price"]
            exit_price = None
            exit_reason = None
            if open_price <= stop_price:
                exit_price, exit_reason = open_price, "損切り条件成立（窓開け）"
            elif open_price >= take_price:
                exit_price, exit_reason = open_price, "利益確定条件成立（窓開け）"
            else:
                stop_hit = low <= stop_price
                take_hit = high >= take_price
                if stop_hit:
                    exit_price, exit_reason = stop_price, "損切り条件成立"
                elif take_hit:
                    exit_price, exit_reason = take_price, "利益確定条件成立"
            if exit_reason is None and position["held_sessions"] >= position["maximum_holding_days"]:
                exit_price, exit_reason = close, "最大保有期間到達"
            if exit_reason is None:
                continue
            slippage = position["slippage_rate"]
            execution_price = float(exit_price) * (
                1 - position["spread_rate"] / 2 - slippage
            )
            gross = execution_price * position["quantity"]
            fee = gross * assumptions.fee_rate
            pre_tax_pnl = gross - fee - position["cost"]
            tax = max(pre_tax_pnl, 0.0) * assumptions.tax_rate
            proceeds = gross - fee - tax
            pnl = proceeds - position["cost"]
            cash += proceeds
            realized_pnl += pnl
            if pnl < 0:
                loss_streak += 1
                if loss_streak >= risk_rules.loss_streak_threshold:
                    cooldown_until_index = session_index + risk_rules.cooldown_sessions
                    loss_streak = 0
            else:
                loss_streak = 0
            action = (
                "損切り"
                if exit_reason.startswith("損切り")
                else "利益確定"
                if exit_reason.startswith("利益確定")
                else "保有期限決済"
            )
            transactions.append(
                {
                    "account": account_name,
                    "date": current_date,
                    "action": action,
                    "symbol": symbol,
                    "name": position.get("name", symbol),
                    "quantity": position["quantity"],
                    "execution_price": execution_price,
                    "amount": proceeds,
                    "fee": fee,
                    "tax": tax,
                    "realized_pnl": pnl,
                    "trade_return": pnl / position["cost"],
                    "reason": exit_reason,
                    "decision_as_of": position["signal_date"],
                    "participation_rate": None,
                    "slippage_rate": slippage,
                    "spread_rate": position["spread_rate"],
                    "execution_cost_profile": position["execution_cost_profile"],
                    "previous_turnover": position["previous_turnover"],
                }
            )
            card = decision_card(
                position,
                status="closed",
                manifest=manifest,
                entry_price=position["entry_execution_price"],
                exit_price=execution_price,
                outcome_reason=exit_reason,
                event_at=current_date,
            )
            cards.append(card)
            del positions[symbol]

        market_value = 0.0
        for symbol, position in positions.items():
            bar = price_lookup.get((symbol, current_date))
            valuation = position["entry_execution_price"]
            if bar is not None and pd.notna(bar.get("close")):
                valuation = float(bar["close"])
            market_value += valuation * position["quantity"]
        cost_basis = sum(position["cost"] for position in positions.values())
        equity = cash + market_value
        high_watermark = max(high_watermark, equity)
        drawdown = equity / high_watermark - 1
        if drawdown <= -risk_rules.maximum_drawdown:
            risk_halted = True
        snapshots.append(
            {
                "date": current_date,
                "cash": cash,
                "market_value": market_value,
                "equity": equity,
                "realized_pnl": realized_pnl,
                "unrealized_pnl": market_value - cost_basis,
                "drawdown": drawdown,
                "risk_halted": risk_halted,
                "cooldown_until_index": cooldown_until_index,
            }
        )

    transaction_frame = pd.DataFrame(transactions)
    snapshot_frame = pd.DataFrame(snapshots)
    closed = (
        transaction_frame[transaction_frame["action"].isin(["利益確定", "損切り", "保有期限決済"])]
        if not transaction_frame.empty
        else pd.DataFrame()
    )
    total_return = float(snapshot_frame.iloc[-1]["equity"] / initial_cash - 1)
    entry_rows = (
        transaction_frame[transaction_frame["action"] == "仮想エントリー"]
        if not transaction_frame.empty
        else pd.DataFrame()
    )
    benchmark_start = (
        pd.to_datetime(entry_rows["date"], utc=True).min()
        if not entry_rows.empty
        else snapshot_frame.iloc[0]["date"]
    )
    benchmark_inputs = {}
    if benchmark is not None:
        benchmark_inputs["primary"] = benchmark
    benchmark_inputs.update(benchmarks or {})
    benchmark_rows = []
    for benchmark_name, benchmark_values in benchmark_inputs.items():
        compared_return = _benchmark_return(
            benchmark_values, benchmark_start, snapshot_frame.iloc[-1]["date"]
        )
        benchmark_rows.append(
            {
                "benchmark": str(benchmark_name),
                "start": benchmark_start,
                "end": snapshot_frame.iloc[-1]["date"],
                "benchmark_return": compared_return,
                "strategy_return": total_return,
                "excess_return": (
                    None if compared_return is None else total_return - compared_return
                ),
            }
        )
    benchmark_comparisons = pd.DataFrame(benchmark_rows)
    primary_comparison = (
        benchmark_comparisons.iloc[0] if not benchmark_comparisons.empty else None
    )
    benchmark_return = (
        None if primary_comparison is None else primary_comparison["benchmark_return"]
    )
    metrics = {
        "total_return": total_return,
        "maximum_drawdown": float(snapshot_frame["drawdown"].min()),
        "closed_trades": int(len(closed)),
        "win_rate": float((closed["realized_pnl"] > 0).mean()) if not closed.empty else None,
        "average_trade_return": float(closed["trade_return"].mean()) if not closed.empty else None,
        "win_rate_ci95": (
            _wilson_interval_95(int((closed["realized_pnl"] > 0).sum()), len(closed))
            if not closed.empty
            else None
        ),
        "average_trade_return_ci95": (
            _mean_confidence_interval_95(closed["trade_return"])
            if not closed.empty
            else None
        ),
        "benchmark_return": benchmark_return,
        "excess_return": None if benchmark_return is None else total_return - benchmark_return,
    }
    position_frame = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "name": position.get("name", symbol),
                "quantity": position["quantity"],
                "entry_date": position["entry_date"],
                "entry_price": position["entry_execution_price"],
                "market_value": _last_close_or_entry(
                    price_lookup,
                    symbol,
                    sessions[-1],
                    position["entry_execution_price"],
                )
                * position["quantity"],
                "unrealized_pnl": _last_close_or_entry(
                    price_lookup,
                    symbol,
                    sessions[-1],
                    position["entry_execution_price"],
                )
                * position["quantity"]
                - position["cost"],
                "sector": position["sector"],
            }
            for symbol, position in positions.items()
        ]
    )
    decision_card_frame = pd.DataFrame(cards)
    if not decision_card_frame.empty:
        decision_card_frame = decision_card_frame.sort_values("event_at").reset_index(
            drop=True
        )
    return {
        "account_name": account_name,
        "initial_cash": float(initial_cash),
        "cash": float(snapshot_frame.iloc[-1]["cash"]),
        "equity": float(snapshot_frame.iloc[-1]["equity"]),
        "realized_pnl": float(snapshot_frame.iloc[-1]["realized_pnl"]),
        "unrealized_pnl": float(snapshot_frame.iloc[-1]["unrealized_pnl"]),
        "positions": position_frame,
        "transactions": transaction_frame,
        "snapshots": snapshot_frame,
        "rejected_signals": pd.DataFrame(rejected),
        "decision_cards": decision_card_frame,
        "benchmark_comparisons": benchmark_comparisons,
        "metrics": metrics,
        "manifest": manifest,
        "assumptions": assumptions,
        "market_impact": market_impact,
        "risk_rules": risk_rules,
        "risk_halted": risk_halted,
    }
