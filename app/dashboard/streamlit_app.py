from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app.analysis.fundamentals import derive_fundamental_metrics
from app.analysis.demo_portfolio import run_demo_portfolio_environment
from app.database.repositories import (
    latest_correlation_results,
    latest_fetch_logs,
    latest_job_runs,
    list_assets_by_source,
)
from app.database.session import SessionLocal
from app.core.config import get_settings
from app.services.analysis_service import (
    DEFAULT_SYMBOLS,
    build_analysis_status,
    load_market_analysis,
    load_market_status,
    load_movement_and_virtual_trade_analysis,
    load_short_term_analysis,
    load_us_japan_spillover_analysis,
    load_sector_sensitivity_analysis,
    load_asset_screening_analysis,
    load_fundamental_snapshots,
    list_fundamental_symbols,
    load_etf_metric_snapshots,
)


JST = ZoneInfo("Asia/Tokyo")


st.set_page_config(page_title="Market Signal Lab", page_icon=":chart_with_upwards_trend:", layout="wide")


def format_percent(value) -> str:
    if pd.isna(value):
        return "-"
    return f"{value:.2%}"


def format_reason_list(value) -> str:
    if not value:
        return "-"
    return " / ".join(str(item) for item in value)


def format_jst(value) -> str:
    if value is None or pd.isna(value):
        return "-"
    return pd.to_datetime(value, utc=True).tz_convert(JST).strftime("%Y-%m-%d %H:%M")


@st.cache_data(ttl=300)
def load_data() -> dict:
    with SessionLocal() as session:
        return load_market_analysis(session, DEFAULT_SYMBOLS)


@st.cache_data(ttl=300)
def load_short_data(symbol: str) -> dict:
    with SessionLocal() as session:
        return load_short_term_analysis(session, symbol)


@st.cache_data(ttl=300)
def load_movement_data(score_threshold: int, holding_days: int) -> dict:
    with SessionLocal() as session:
        return load_movement_and_virtual_trade_analysis(
            session,
            score_threshold=score_threshold,
            holding_days=holding_days,
        )


@st.cache_data(ttl=300)
def load_spillover_data(base_symbol: str, target_symbol: str) -> dict:
    with SessionLocal() as session:
        return load_us_japan_spillover_analysis(session, base_symbol, target_symbol)


@st.cache_data(ttl=600)
def load_sensitivity_data(base_symbol: str) -> dict:
    with SessionLocal() as session:
        return load_sector_sensitivity_analysis(session, base_symbol=base_symbol)


@st.cache_data(ttl=600)
def load_screening_data() -> dict:
    with SessionLocal() as session:
        return load_asset_screening_analysis(session)


@st.cache_data(ttl=600)
def load_fundamentals_data(symbol: str) -> pd.DataFrame:
    with SessionLocal() as session:
        return load_fundamental_snapshots(session, symbol)


@st.cache_data(ttl=600)
def load_fundamental_symbol_options() -> list[str]:
    with SessionLocal() as session:
        return list_fundamental_symbols(session)


@st.cache_data(ttl=600)
def load_etf_metrics_data(symbol: str) -> pd.DataFrame:
    with SessionLocal() as session:
        return load_etf_metric_snapshots(session, symbol)


@st.cache_data(ttl=300)
def load_status_data() -> dict:
    with SessionLocal() as session:
        return load_market_status(session, DEFAULT_SYMBOLS)


@st.cache_data
def load_demo_portfolio() -> dict:
    return run_demo_portfolio_environment()


PAGE_OPTIONS = [
    "市場ダッシュボード",
    "短期分析",
    "銘柄・ETF分析",
    "変動候補",
    "仮想投資評価",
    "市場連動性",
    "日米波及分析",
    "システム管理",
]


st.title("Market Signal Lab")
st.caption("短期取引と中期投資の判断材料を整理する分析アプリです。自動売買や投資助言は行いません。")
settings = get_settings()
is_demo = settings.market_data_mode == "demo"
if is_demo:
    st.warning("デモモード: 合成データのみを表示しています。投資判断には使用できません。")

active_page = st.radio(
    "表示画面",
    PAGE_OPTIONS,
    index=PAGE_OPTIONS.index("仮想投資評価") if is_demo else 0,
    horizontal=True,
    label_visibility="collapsed",
    key="active_page",
)

analysis = load_data() if active_page in {"市場ダッシュボード", "市場連動性"} else None
if analysis is not None:
    analysis_status = build_analysis_status(analysis["prices"], analysis["data_quality_warnings"])
    data_quality_warnings = analysis["data_quality_warnings"]
elif is_demo and active_page == "仮想投資評価":
    analysis_status = {
        "mode": "demo",
        "source_policy": "demo_only",
        "period_start": None,
        "period_end": None,
        "latest_fetched_at": None,
        "price_bases": ["synthetic_demo"],
        "warning_count": 1,
    }
    data_quality_warnings = [
        {"message": "合成価格・合成ニュースだけを使用する検証画面です。実データではありません。"}
    ]
else:
    status_data = load_status_data()
    analysis_status = status_data["status"]
    data_quality_warnings = status_data["warnings"]

with st.expander("データ品質・分析の現在地", expanded=True):
    status_cols = st.columns(4)
    status_cols[0].metric("モード", "デモ" if analysis_status["mode"] == "demo" else "通常")
    status_cols[1].metric("品質警告", analysis_status["warning_count"])
    status_cols[2].metric("最終データ日", format_jst(analysis_status["period_end"]))
    status_cols[3].metric("最終取得", format_jst(analysis_status["latest_fetched_at"]))
    st.caption(
        f"source方針: {analysis_status['source_policy']} / 価格基準: "
        f"{', '.join(analysis_status['price_bases']) or '未取得'} / "
        f"対象期間: {format_jst(analysis_status['period_start'])}〜{format_jst(analysis_status['period_end'])}"
    )
    if data_quality_warnings:
        for warning in data_quality_warnings:
            st.warning(warning["message"])

if active_page == "市場ダッシュボード":
    wide = analysis["wide"]
    normalized = analysis["normalized"]
    prices = analysis["prices"]
    if wide.empty:
        st.warning("実データがありません。FREDまたはJ-Quantsの収集ジョブを実行してください。")
    else:
        if data_quality_warnings:
            for warning in data_quality_warnings:
                st.warning(warning["message"])

        latest = wide.tail(1).T.reset_index()
        latest.columns = ["symbol", "latest_close"]
        latest["daily_return"] = wide.pct_change(fill_method=None).tail(1).T.iloc[:, 0].to_numpy()

        cols = st.columns(5)
        for col, row in zip(cols, latest.to_dict(orient="records"), strict=False):
            col.metric(row["symbol"], f"{row['latest_close']:,.2f}", format_percent(row["daily_return"]))

        st.subheader("指数比較")
        chart_data = normalized.reset_index().melt(id_vars="price_time", var_name="symbol", value_name="index")
        fig = px.line(chart_data, x="price_time", y="index", color="symbol", labels={"index": "開始日=100"})
        st.plotly_chart(fig, use_container_width=True)

        provenance = analysis["input_provenance"]
        st.info("欠損日は前方補完せず、取得できた観測値だけで計算しています。相関は因果関係を示すものではありません。")
        st.caption(
            f"分析入力: source方針 {provenance['source_policy_version']} / 入力版 {provenance['input_data_version'][:12]}… / "
            f"価格基準 {provenance['input_provenance']['price_basis']}"
        )

        source_view = (
            prices.sort_values("fetched_at")
            .groupby("symbol")
            .tail(1)[
                ["symbol", "source", "source_symbol", "price_time", "fetched_at", "data_quality_status"]
            ]
            .assign(data_as_of_jst=lambda df: df["price_time"].map(format_jst))
            .assign(fetched_at_jst=lambda df: df["fetched_at"].map(format_jst))
        )
        st.dataframe(
            source_view[
                [
                    "symbol",
                    "source",
                    "source_symbol",
                    "data_as_of_jst",
                    "fetched_at_jst",
                    "data_quality_status",
                ]
            ],
            use_container_width=True,
        )

if active_page == "短期分析":
    st.subheader("短期分析")
    selected_symbol = st.selectbox("対象", DEFAULT_SYMBOLS, index=0)
    short = load_short_data(selected_symbol)
    indicators = short["indicators"]
    if indicators.empty:
        st.warning("短期分析に必要な価格データがありません。")
    else:
        snapshot = short["snapshot"]
        latest = indicators.dropna(subset=["close"]).iloc[-1]
        metrics = st.columns(4)
        metrics[0].metric("判定", snapshot["label"])
        metrics[1].metric("短期スコア", f"{snapshot['score']} / 100")
        metrics[2].metric("終値", f"{latest['close']:,.2f}")
        metrics[3].metric("20日騰落率", format_percent(latest.get("return_20d")))

        chart_frame = indicators.tail(260).reset_index().rename(columns={"index": "price_time"})
        price_fig = go.Figure()
        price_fig.add_trace(
            go.Scatter(x=chart_frame["price_time"], y=chart_frame["close"], name="終値", mode="lines")
        )
        for column, label in [("sma_5", "5日MA"), ("sma_20", "20日MA"), ("sma_50", "50日MA"), ("sma_75", "75日MA")]:
            price_fig.add_trace(
                go.Scatter(x=chart_frame["price_time"], y=chart_frame[column], name=label, mode="lines")
            )
        price_fig.add_trace(
            go.Scatter(
                x=chart_frame["price_time"],
                y=chart_frame["bb_upper"],
                name="BB上限",
                mode="lines",
                line={"dash": "dot"},
            )
        )
        price_fig.add_trace(
            go.Scatter(
                x=chart_frame["price_time"],
                y=chart_frame["bb_lower"],
                name="BB下限",
                mode="lines",
                line={"dash": "dot"},
            )
        )
        price_fig.update_layout(xaxis_title="日付", yaxis_title="価格")
        st.plotly_chart(price_fig, use_container_width=True)

        rsi_fig = go.Figure()
        rsi_fig.add_trace(
            go.Scatter(x=chart_frame["price_time"], y=chart_frame["rsi_14"], name="RSI 14", mode="lines")
        )
        rsi_fig.add_hline(y=70, line_dash="dot")
        rsi_fig.add_hline(y=30, line_dash="dot")
        rsi_fig.update_layout(xaxis_title="日付", yaxis_title="RSI")
        st.plotly_chart(rsi_fig, use_container_width=True)

        macd_fig = go.Figure()
        macd_fig.add_trace(
            go.Scatter(x=chart_frame["price_time"], y=chart_frame["macd"], name="MACD", mode="lines")
        )
        macd_fig.add_trace(
            go.Scatter(x=chart_frame["price_time"], y=chart_frame["signal"], name="シグナル", mode="lines")
        )
        macd_fig.add_trace(
            go.Bar(x=chart_frame["price_time"], y=chart_frame["histogram"], name="ヒストグラム")
        )
        macd_fig.update_layout(xaxis_title="日付", yaxis_title="MACD")
        st.plotly_chart(macd_fig, use_container_width=True)

        reason_cols = st.columns(2)
        with reason_cols[0]:
            st.markdown("**加点要因**")
            st.write(snapshot["positive_reasons"] or ["目立った加点要因はありません"])
        with reason_cols[1]:
            st.markdown("**減点要因**")
            st.write(snapshot["negative_reasons"] or ["目立った減点要因はありません"])

        st.caption(
            "FRED由来の指数データは高値、安値、出来高を含まないため、ローソク足、出来高、ATRは今後のデータソース追加後に表示します。"
        )
        st.caption(f"データソース: {short.get('source', '-')} / 最終取得: {format_jst(short.get('fetched_at'))}")

if active_page == "銘柄・ETF分析":
    st.subheader("銘柄・ETFスクリーニング")
    st.caption("観測済みの価格データから技術指標を比較します。財務値や将来価格は推測せず、投資推奨は行いません。")
    st.warning(
        "J-Quants Free planの価格は約12週間遅延しています。"
        "この一覧は過去データ上の分析候補であり、現在の短期売買判断には使用できません。"
    )
    screening = load_screening_data()["screening"]
    if screening.empty:
        st.warning("スクリーニングには、銘柄マスターと東証カレンダー上で連続する30営業日以上の有効なJ-Quants調整済み価格履歴が必要です。")
    else:
        filter_cols = st.columns(3)
        asset_type = filter_cols[0].multiselect(
            "対象区分", ["stock", "etf"], default=["stock", "etf"]
        )
        minimum_attention = filter_cols[1].slider(
            "最低注目度", min_value=0, max_value=100, value=50, step=5
        )
        sector_options = sorted(screening["sector"].dropna().unique().tolist())
        selected_sectors = filter_cols[2].multiselect("業種（未選択はすべて）", sector_options)
        view = screening[
            screening["asset_type"].isin(asset_type)
            & (screening["attention_score"] >= minimum_attention)
        ].copy()
        if selected_sectors:
            view = view[view["sector"].isin(selected_sectors)]
        view["data_as_of"] = view["data_as_of"].map(
            lambda value: "-" if pd.isna(value) else pd.Timestamp(value).strftime("%Y-%m-%d")
        )
        view["attention_reasons"] = view["attention_reasons"].map(format_reason_list)
        view["quality_warnings"] = view["quality_warnings"].map(format_reason_list)
        view["return_20d"] = view["return_20d"].map(format_percent)
        view["volatility_20d"] = view["volatility_20d"].map(format_percent)
        view["rsi_14"] = view["rsi_14"].round(1)
        st.metric("表示中の分析候補", len(view))
        if view.empty:
            st.info("現在の条件に一致する分析候補はありません。条件を緩めて確認してください。")
        else:
            st.dataframe(view, use_container_width=True)
        st.caption(
            "東証カレンダー上で連続する30営業日以上の有効な調整済み価格を持つ銘柄のうち、履歴が充実した最大200銘柄を比較しています。"
            "注目度は値動きの大きさや指標の偏りを示すもので、上昇確率や推奨順位ではありません。"
            "50日・75日移動平均は、それぞれ必要な観測数がそろった銘柄だけで利用します。"
        )
        financial_symbols = sorted(
            set(screening["symbol"].tolist()) | set(load_fundamental_symbol_options())
        )
        selected_financial_symbol = st.selectbox("財務サマリーを表示", financial_symbols)
        financials = load_fundamentals_data(selected_financial_symbol)
        if financials.empty:
            st.info("財務サマリーは未取得です。")
        else:
            st.subheader("開示済み財務サマリー")
            st.caption("開示日時以前の情報だけを履歴分析へ使用します。未取得項目は推測しません。")
            st.dataframe(financials, use_container_width=True)
            latest_financial = financials.iloc[-1].to_dict()
            latest_price = view.loc[view["symbol"] == selected_financial_symbol, "latest_close"]
            derived = derive_fundamental_metrics(
                latest_financial,
                float(latest_price.iloc[0]) if not latest_price.empty else None,
            )
            metric_cols = st.columns(4)
            for col, (label, key, suffix) in zip(
                metric_cols,
                [
                    ("PER", "per", "倍"),
                    ("PBR", "pbr", "倍"),
                    ("ROE", "roe", "%"),
                    ("営業利益率", "operating_margin", "%"),
                ],
            ):
                value = derived.get(key)
                if value is None:
                    display = "-"
                elif suffix == "%":
                    display = f"{value:.2f}%"
                else:
                    display = f"{value:.2f}{suffix}"
                col.metric(label, display)
            st.caption("表示値は取得済みの開示情報と最新価格から決定論的に算出しています。未取得項目は推測しません。")
        if selected_financial_symbol in set(view.loc[view["asset_type"] == "etf", "symbol"]):
            etf_metrics = load_etf_metrics_data(selected_financial_symbol)
            st.subheader("ETF固有指標")
            if etf_metrics.empty:
                st.info("ETF固有指標は未取得です。取得元に存在しない項目は推測しません。")
            else:
                st.dataframe(etf_metrics, use_container_width=True)
                st.caption("取得済みの提供元データのみ表示しています。")

if active_page == "変動候補":
    st.subheader("大きく動きそうな日本株・ETF候補")
    st.caption(
        "米国指数と日経平均の相関、市場の直近変動、個別銘柄の短期指標、過去の仮想投資フィードバックから候補を抽出します。投資助言ではありません。"
    )
    movement_data = load_movement_data(score_threshold=70, holding_days=5)
    movement = movement_data["movement"]
    us_signal = movement["us_signal"]
    cols = st.columns(3)
    cols[0].metric("米国指数の直近方向", us_signal["direction"])
    cols[1].metric("米国指数平均騰落率", format_percent(us_signal["average_return"]))
    cols[2].metric(
        "米国・日本指数の平均相関",
        "-" if pd.isna(movement["average_correlation"]) else f"{movement['average_correlation']:.3f}",
    )

    with st.expander("市場背景の根拠"):
        st.write(us_signal["reasons"] or ["米国指数データが不足しています"])
        if movement["pair_summaries"]:
            st.dataframe(movement["pair_summaries"], use_container_width=True)

    candidates = movement["candidates"]
    valid_history_count = movement.get("eligible_count", 0)
    st.caption(
        f"候補判定可能な最新連続履歴30営業日以上の銘柄数: {valid_history_count}"
    )
    if candidates.empty:
        st.warning("候補はまだありません。候補判定には各銘柄について東証カレンダー上で最新から連続する30営業日以上の有効な調整済み価格履歴が必要です。legacy_unknownの価格は品質保護のため判定に使用しません。")
        insufficient = movement.get("insufficient", pd.DataFrame())
        if not insufficient.empty:
            st.caption("現在の候補対象と履歴件数")
            st.dataframe(insufficient.head(20), use_container_width=True)
    else:
        view = candidates.copy()
        for column in ["return_1d", "return_5d", "return_20d", "volatility_20d"]:
            view[column] = view[column].map(format_percent)
        view["rsi_14"] = view["rsi_14"].map(lambda value: "-" if pd.isna(value) else f"{value:.1f}")
        view["reasons"] = view["reasons"].map(format_reason_list)
        st.dataframe(
            view[
                [
                    "symbol",
                    "name",
                    "score",
                    "direction",
                    "latest_close",
                    "return_5d",
                    "volatility_20d",
                    "rsi_14",
                    "feedback_score",
                    "reasons",
                    "observations",
                ]
            ],
            use_container_width=True,
        )

    insufficient = movement["insufficient"]
    if not insufficient.empty:
        with st.expander("データ不足で評価できない銘柄"):
            st.dataframe(insufficient, use_container_width=True)

if active_page == "仮想投資評価":
    st.subheader("仮想投資評価")
    st.caption("実際の投資や注文は行いません。過去時点で候補に出たと仮定し、一定営業日後の損益と理由を検証します。")
    if is_demo:
        st.warning(
            "検証用デモ: 合成価格・合成ニュースだけで計算します。実績、予測、投資判断には使用できません。"
        )
        if st.button("短期・中期のデモ仮想口座を実行", type="primary"):
            st.session_state["demo_portfolio_environment"] = load_demo_portfolio()
        demo_environment = st.session_state.get("demo_portfolio_environment")
        if demo_environment:
            assumptions = demo_environment["assumptions"]
            st.caption(demo_environment["warning"])
            st.info(
                "約定仮定: "
                f"{assumptions['execution_rule']} / 手数料 {assumptions['fee_rate']:.2%} / "
                f"スプレッド {assumptions['spread_rate']:.2%} / 税率 {assumptions['tax_rate']:.1%} / "
                f"売買単位 {assumptions['lot_size']}株 / 同時保有上限 {assumptions['maximum_positions']}銘柄"
            )
            for account in demo_environment["accounts"].values():
                st.markdown(f"### {account['label']}口座")
                metrics = st.columns(6)
                metrics[0].metric("初期資金", f"¥{account['initial_cash']:,.0f}")
                metrics[1].metric("現金", f"¥{account['cash']:,.0f}")
                metrics[2].metric("評価額", f"¥{account['equity']:,.0f}")
                metrics[3].metric("実現損益", f"¥{account['realized_pnl']:+,.0f}")
                metrics[4].metric("未実現損益", f"¥{account['unrealized_pnl']:+,.0f}")
                metrics[5].metric("最大ドローダウン", format_percent(account["maximum_drawdown"]))

                snapshots = account["snapshots"].copy()
                if not snapshots.empty:
                    balance = snapshots.set_index("date")[["equity"]].rename(columns={"equity": "口座残高"})
                    st.line_chart(balance)

                positions = account["positions"]
                if positions.empty:
                    st.caption("現在の仮想保有はありません。")
                else:
                    with st.expander("現在の仮想保有", expanded=True):
                        st.dataframe(positions, use_container_width=True, hide_index=True)

                transactions = account["transactions"].copy()
                with st.expander(f"取引履歴（{len(transactions)}件）"):
                    if transactions.empty:
                        st.info("取引条件に一致する仮想取引はありません。")
                    else:
                        st.dataframe(
                            transactions[
                                [
                                    "date",
                                    "action",
                                    "symbol",
                                    "quantity",
                                    "execution_price",
                                    "realized_pnl",
                                    "reason",
                                    "decision_as_of",
                                ]
                            ],
                            use_container_width=True,
                            hide_index=True,
                        )

                latest_signals = account["latest_signals"].copy()
                if not latest_signals.empty:
                    with st.expander("直近の検証用シグナル"):
                        latest_signals["reasons"] = latest_signals["reasons"].map(format_reason_list)
                        latest_signals["news_headlines"] = latest_signals["news_headlines"].map(
                            format_reason_list
                        )
                        st.dataframe(latest_signals, use_container_width=True, hide_index=True)
    else:
        st.info("通常モードでは、各銘柄について東証カレンダー上で最新から連続する30営業日以上の有効な調整済み履歴がそろうまで仮想評価を生成しません。")
        threshold = st.slider("仮想エントリーの最低スコア", min_value=50, max_value=90, value=70, step=5)
        holding_days = st.selectbox("仮想保有期間", [1, 5, 10, 20], index=1)
        virtual_data = load_movement_data(score_threshold=threshold, holding_days=holding_days)
        trades = virtual_data["virtual_trades"]
        feedback = virtual_data["virtual_feedback"]
        st.caption("仮想投資の成績は銘柄別に集計され、変動候補画面のフィードバック指標として次回の抽出に反映されます。")
        if trades.empty:
            st.warning("仮想投資評価に必要な履歴データが不足しています。東証カレンダー上で最新から連続する30営業日以上の日本株・ETFデータが必要です。")
        else:
            summary_cols = st.columns(4)
            summary_cols[0].metric("仮想件数", len(trades))
            summary_cols[1].metric("平均損益", format_percent(trades["return"].mean()))
            summary_cols[2].metric("勝率", format_percent((trades["return"] > 0).mean()))
            summary_cols[3].metric("最大損益", format_percent(trades["return"].max()))

            view = trades.copy()
            view["signal_date"] = view["signal_date"].map(lambda value: pd.to_datetime(value).strftime("%Y-%m-%d"))
            view["exit_date"] = view["exit_date"].map(lambda value: pd.to_datetime(value).strftime("%Y-%m-%d"))
            view["return"] = view["return"].map(format_percent)
            view["entry_reasons"] = view["entry_reasons"].map(format_reason_list)
            view["outcome_reasons"] = view["outcome_reasons"].map(format_reason_list)
            st.dataframe(
                view[
                    [
                        "signal_date",
                        "exit_date",
                        "symbol",
                        "name",
                        "score",
                        "direction",
                        "return",
                        "outcome",
                        "entry_reasons",
                        "outcome_reasons",
                    ]
                ],
                use_container_width=True,
            )

            if feedback:
                with st.expander("候補抽出に戻すフィードバック集計"):
                    feedback_view = pd.DataFrame(
                        [{"symbol": symbol, **values} for symbol, values in feedback.items()]
                    )
                    feedback_view["win_rate"] = feedback_view["win_rate"].map(format_percent)
                    feedback_view["average_return"] = feedback_view["average_return"].map(format_percent)
                    feedback_view["large_move_rate"] = feedback_view["large_move_rate"].map(format_percent)
                    st.dataframe(feedback_view, use_container_width=True)

if active_page == "市場連動性":
    st.subheader("NASDAQ Composite 前営業日と日経平均 当日の対応")
    pair = analysis["pair"]
    if pair.empty:
        st.warning("相関分析に必要なデータが不足しています。")
    else:
        corr = analysis["horizon_correlations"].copy()
        corr["correlation"] = corr["correlation"].map(lambda value: "-" if pd.isna(value) else f"{value:.3f}")
        st.dataframe(corr, use_container_width=True)

        rolling = analysis["rolling_correlation"]
        if not rolling.empty:
            rolling = rolling.rename("correlation")
            rolling_fig = px.line(
                rolling.reset_index(),
                x="japan_date",
                y="correlation",
                labels={"japan_date": "日本市場日", "correlation": "60日ローリング相関"},
            )
            st.plotly_chart(rolling_fig, use_container_width=True)

        scatter = pair.copy()
        scatter["us_return_pct"] = scatter["us_return"] * 100
        scatter["japan_return_pct"] = scatter["japan_return"] * 100
        fig = px.scatter(
            scatter,
            x="us_return_pct",
            y="japan_return_pct",
            trendline="ols",
            labels={"us_return_pct": "米国前営業日リターン(%)", "japan_return_pct": "日本当日リターン(%)"},
        )
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("条件別分析")
        st.dataframe(analysis["conditional_stats"], use_container_width=True)
        st.caption("統計的傾向の表示であり、将来の値動きや利益を保証するものではありません。")

if active_page == "日米波及分析":
    st.subheader("米国前営業日から日本当日への波及")
    st.caption(
        "米国指数は前営業日の終値リターン、日本株・ETFは実際の始値・終値から算出した寄り付きギャップ・場中・日次リターンを対応させます。因果関係や将来の値動きを示すものではありません。"
    )
    with SessionLocal() as session:
        jquants_assets = list_assets_by_source(session, "jquants", asset_types=["stock", "etf"])
    if not jquants_assets:
        st.warning("J-Quantsの銘柄マスターが未取得です。銘柄と日次OHLCを取得後に分析できます。")
    else:
        target_symbols = [asset.symbol for asset in jquants_assets]
        base_symbol = st.selectbox("米国指数", ["NASDAQCOM", "DJIA", "SP500"], key="spillover_us")
        target_symbol = st.selectbox("日本株・ETF", target_symbols, key="spillover_jp")
        spillover = load_spillover_data(base_symbol, target_symbol)
        provenance = spillover["input_provenance"]
        st.caption(
            f"分析入力: source方針 {provenance['source_policy_version']} / 入力版 {provenance['input_data_version'][:12]}… / "
            f"価格基準 {provenance['input_provenance']['price_basis']}"
        )
        for warning in spillover["warnings"]:
            st.warning(warning)
        frame = spillover["frame"]
        if not frame.empty:
            metrics = st.columns(4)
            metrics[0].metric("対応セッション数", len(frame))
            metrics[1].metric("平均寄り付きギャップ", format_percent(frame["gap_return"].mean()))
            metrics[2].metric("平均場中リターン", format_percent(frame["intraday_return"].mean()))
            metrics[3].metric("平均日次リターン", format_percent(frame["daily_return"].mean()))

            plot_data = frame.copy()
            plot_data["us_return_pct"] = plot_data["us_return"] * 100
            plot_data["gap_return_pct"] = plot_data["gap_return"] * 100
            figure = px.scatter(
                plot_data.dropna(subset=["gap_return_pct"]),
                x="us_return_pct",
                y="gap_return_pct",
                labels={"us_return_pct": "米国前営業日リターン(%)", "gap_return_pct": "日本当日寄り付きギャップ(%)"},
            )
            st.plotly_chart(figure, use_container_width=True)

            for metric, label in [
                ("gap_return", "寄り付きギャップ"),
                ("intraday_return", "場中リターン"),
                ("daily_return", "日次リターン"),
            ]:
                with st.expander(f"米国前営業日リターン別の {label}"):
                    stats = spillover["conditional_stats"][metric].copy()
                    for column in ["mean_return", "median_return", "positive_rate"]:
                        stats[column] = stats[column].map(format_percent)
                    st.dataframe(stats, use_container_width=True)
                with st.expander(f"{label} のラグ回帰・ローリング検証"):
                    regression = spillover["regression"][metric]
                    full = regression["full"]
                    if full["status"] != "ok":
                        st.info("回帰には少なくとも10件の対応セッションが必要です。")
                    else:
                        coefficient = full["coefficients"].get("us_return")
                        p_value = full["p_values"].get("us_return")
                        confidence = full["confidence_intervals_95"].get("us_return", [None, None])
                        regression_metrics = st.columns(4)
                        regression_metrics[0].metric("サンプル数", full["sample_size"])
                        regression_metrics[1].metric("決定係数 R²", f"{full['r_squared']:.3f}")
                        regression_metrics[2].metric("米国リターン係数", f"{coefficient:.3f}")
                        regression_metrics[3].metric("p値", f"{p_value:.3f}")
                        st.caption(
                            f"係数の95%信頼区間: [{confidence[0]:.3f}, {confidence[1]:.3f}]。"
                        )
                        window_rows = []
                        for row in regression["windows"]:
                            if row["status"] == "ok":
                                window_rows.append(
                                    {
                                        "window_days": row["window_days"],
                                        "sample_size": row["sample_size"],
                                        "r_squared": row["r_squared"],
                                        "us_return_coefficient": row["coefficients"].get("us_return"),
                                        "p_value": row["p_values"].get("us_return"),
                                    }
                                )
                        if window_rows:
                            st.dataframe(pd.DataFrame(window_rows), use_container_width=True)
                        rolling = regression["rolling"].get(20, pd.DataFrame())
                        if not rolling.empty:
                            rolling_figure = px.line(
                                rolling,
                                x="period_end",
                                y="us_return",
                                labels={"period_end": "日本市場日", "us_return": "20日ローリング回帰係数"},
                            )
                            st.plotly_chart(rolling_figure, use_container_width=True)
                        walk_forward = regression.get("walk_forward", pd.DataFrame())
                        if not walk_forward.empty:
                            st.caption("ウォークフォワード検証（予測時点より前の観測だけで学習）")
                            st.dataframe(walk_forward.tail(20), use_container_width=True)
                    granger = regression["granger"]
                    st.markdown("予測上の先行性（Granger検定）")
                    if granger["status"] != "ok":
                        st.info("Granger検定には少なくとも30件の対応セッションが必要です。")
                    else:
                        granger_rows = [
                            {
                                "lag": lag,
                                "F統計量": values["ssr_ftest_statistic"],
                                "p値": values["ssr_ftest_p_value"],
                                "補正後p値": values.get("adjusted_p_value"),
                            }
                            for lag, values in granger["lag_results"].items()
                        ]
                        st.dataframe(pd.DataFrame(granger_rows), use_container_width=True)
                        st.caption(
                            "Granger検定は予測上の先行性の確認であり、因果関係を証明するものではありません。"
                        )
                    st.caption(
                        "回帰は統計的な関連を示すだけで、因果関係、将来の値動き、利益を保証するものではありません。"
                    )
            st.subheader("業種・銘柄感応度（観測値）")
            st.caption("過去の対応セッションにおける統計的な関連を示します。予測や推奨ではありません。標本数が少ない業種・銘柄は除外しています。")
            if st.toggle(
                "業種・銘柄感応度を計算する",
                value=False,
                key="enable_sector_sensitivity",
                help="全対象銘柄を確認するため、必要なときだけ実行します。",
            ):
                sensitivity = load_sensitivity_data(base_symbol)
                sector_view = sensitivity["sensitivity"]["sector"]
                if sector_view.empty:
                    st.info("業種感応度に必要な対応セッションが不足しています。")
                else:
                    st.dataframe(sector_view, use_container_width=True)
            else:
                st.info("画面の通常読み込みを軽くするため、感応度計算は停止しています。")
            st.caption(
                "保存するには `python jobs/run_spillover_analysis.py --jp-symbol <銘柄コード>` を実行します。欠損値は補完せず、始値・終値がある観測日のみ利用します。"
            )

if active_page == "システム管理":
    with SessionLocal() as session:
        fetch_logs = latest_fetch_logs(session)
        job_runs = latest_job_runs(session)
        operations_runs = latest_job_runs(session, limit=5, job_name="check_operations")
        collector_runs = latest_job_runs(
            session, limit=1, job_name="collect_jquants_all_prices"
        )
        correlation_logs = latest_correlation_results(session, analysis_status=None)

    latest_operations = operations_runs[0].details if operations_runs else {}
    latest_collector = collector_runs[0].details if collector_runs else {}
    recent_progress = latest_operations.get("recent_30_session_progress", {})
    st.subheader("J-Quants収集進捗")
    st.caption(
        "この表示は、現在のアプリが接続しているDBの状態です。localhostではMacへ最後に複製した時点、ラズパイ画面ではラズパイの実運用値を表示します。"
    )
    progress_cols = st.columns(4)
    queue_phase_labels = {
        "latest": "最新取引日",
        "recent_gap": "直近30日の欠損",
        "history": "古い履歴",
    }
    queue_phase = latest_collector.get("queue_phase") or latest_operations.get(
        "collection_queue_phase"
    )
    queue_target = latest_collector.get("target_date") or latest_operations.get(
        "collection_queue_target_date"
    )
    progress_cols[0].metric("現在の収集段階", queue_phase_labels.get(queue_phase, "未確認"))
    progress_cols[1].metric("処理対象日", queue_target or "-")
    progress_cols[2].metric(
        "連続30営業日到達銘柄",
        latest_operations.get("adjusted_history_ready_symbols", "-"),
    )
    progress_cols[3].metric(
        "最新連続履歴の最大",
        f"{latest_operations.get('adjusted_history_max_observations', '-')}営業日",
    )
    recent_ratio = recent_progress.get("progress_ratio")
    if recent_ratio is not None:
        st.progress(min(1.0, max(0.0, float(recent_ratio))))
        st.caption(
            "直近30営業日カバー率 "
            f"{float(recent_ratio):.1%} / 完了日 "
            f"{recent_progress.get('complete_sessions', 0)} of "
            f"{recent_progress.get('session_count', 30)} / 残り要求上限 "
            f"{recent_progress.get('remaining_requests_upper_bound', 0):,}件 / "
            f"理論最短 {recent_progress.get('theoretical_minimum_hours', 0):.1f}時間"
        )
    else:
        st.info("直近30営業日の収集進捗は、次回の運用確認後に表示されます。")
    if operations_runs:
        st.caption(f"運用確認時刻: {format_jst(operations_runs[0].started_at)}")

    st.subheader("API取得状況")
    st.dataframe(
        [
            {
                "provider": log.provider,
                "symbol": log.asset_symbol,
                "status": log.status,
                "fetched_at": format_jst(log.fetched_at),
                "latency_ms": log.latency_ms,
                "message": log.message,
            }
            for log in fetch_logs
        ],
        use_container_width=True,
    )

    operations_history = []
    for job in operations_runs:
        if not isinstance(job.details, dict):
            continue
        operations_history.append(
            {
                "checked_at": format_jst(job.started_at),
                "status": job.status,
                "disk_used_ratio": job.details.get("disk_used_ratio"),
                "market_prices": job.details.get("market_prices"),
                "adjusted_history_ready_symbols": job.details.get("adjusted_history_ready_symbols"),
                "adjusted_history_max_observations": job.details.get("adjusted_history_max_observations"),
                "adjusted_history_symbols_by_threshold": job.details.get("adjusted_history_symbols_by_threshold"),
                "raw_observed_history_max_rows": job.details.get(
                    "adjusted_observed_history_max_observations"
                ),
                "raw_observed_history_by_threshold": job.details.get(
                    "adjusted_observed_history_symbols_by_threshold"
                ),
                "collection_targets_progress_ratio": job.details.get("collection_targets_progress_ratio"),
                "collection_next_session_date": job.details.get("collection_next_session_date"),
                "collection_items_retry_pending": job.details.get("collection_items_retry_pending"),
                "price_basis_counts": job.details.get("price_basis_counts"),
                "fundamental_snapshots": job.details.get("fundamental_snapshots"),
                "etf_metric_snapshots": job.details.get("etf_metric_snapshots"),
                "assets_with_sec_cik": job.details.get("assets_with_sec_cik"),
                "latest_fetched_at": format_jst(job.details.get("latest_fetched_at")),
                "failed_or_retry_jobs_24h": job.details.get("failed_or_retry_jobs_24h"),
                "warnings": job.details.get("warnings", []),
            }
        )
    if operations_history:
        st.subheader("運用履歴（ディスク・DB・失敗ジョブ）")
        st.dataframe(pd.DataFrame(operations_history), use_container_width=True)

    st.subheader("ジョブ実行状況")
    st.dataframe(
        [
            {
                "job": job.job_name,
                "status": job.status,
                "started_at": format_jst(job.started_at),
                "finished_at": format_jst(job.finished_at),
                "details": job.details,
            }
            for job in job_runs
        ],
        use_container_width=True,
    )

    st.subheader("保存済み相関分析")
    if any(row.analysis_status == "requires_recalculation" for row in correlation_logs):
        st.warning("入力sourceを復元できない旧結果があります。`requires_recalculation` の行は判断材料に使わないでください。")
    st.dataframe(
        [
            {
                "analysis": row.analysis_name,
                "base": row.base_symbol,
                "target": row.target_symbol,
                "window_days": row.window_days,
                "correlation": None if row.correlation is None else float(row.correlation),
                "sample_size": row.sample_size,
                "period_end": format_jst(row.period_end),
                "computed_at": format_jst(row.computed_at),
                "lag_rule": row.lag_rule,
                "analysis_status": row.analysis_status,
                "source_policy_version": row.source_policy_version,
                "input_data_version": row.input_data_version,
            }
            for row in correlation_logs
        ],
        use_container_width=True,
    )
