from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

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
    load_market_analysis,
    load_movement_and_virtual_trade_analysis,
    load_short_term_analysis,
    load_us_japan_spillover_analysis,
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


st.title("Market Signal Lab")
st.caption("短期取引と中期投資の判断材料を整理する分析アプリです。自動売買や投資助言は行いません。")
if get_settings().market_data_mode == "demo":
    st.warning("デモモード: 合成データのみを表示しています。投資判断には使用できません。")

tab_market, tab_short, tab_candidates, tab_virtual, tab_correlation, tab_spillover, tab_system = st.tabs(
    [
        "市場ダッシュボード",
        "短期分析",
        "変動候補",
        "仮想投資評価",
        "市場連動性",
        "日米波及分析",
        "システム管理",
    ]
)

analysis = load_data()
wide = analysis["wide"]
normalized = analysis["normalized"]
prices = analysis["prices"]
data_quality_warnings = analysis["data_quality_warnings"]

with tab_market:
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

with tab_short:
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

with tab_candidates:
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
    if candidates.empty:
        st.warning("候補抽出に必要な日本株・ETFの履歴データが不足しています。J-Quantsの日次データを複数日分取得してください。")
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

with tab_virtual:
    st.subheader("仮想投資評価")
    st.caption("実際の投資や注文は行いません。過去時点で候補に出たと仮定し、一定営業日後の損益と理由を検証します。")
    threshold = st.slider("仮想エントリーの最低スコア", min_value=50, max_value=90, value=70, step=5)
    holding_days = st.selectbox("仮想保有期間", [1, 5, 10, 20], index=1)
    virtual_data = load_movement_data(score_threshold=threshold, holding_days=holding_days)
    trades = virtual_data["virtual_trades"]
    feedback = virtual_data["virtual_feedback"]
    st.caption("仮想投資の成績は銘柄別に集計され、変動候補画面のフィードバック指標として次回の抽出に反映されます。")
    if trades.empty:
        st.warning("仮想投資評価に必要な履歴データが不足しています。30営業日以上の日本株・ETFデータが必要です。")
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

with tab_correlation:
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

with tab_spillover:
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
            st.caption(
                "保存するには `python jobs/run_spillover_analysis.py --jp-symbol <銘柄コード>` を実行します。欠損値は補完せず、始値・終値がある観測日のみ利用します。"
            )

with tab_system:
    with SessionLocal() as session:
        fetch_logs = latest_fetch_logs(session)
        job_runs = latest_job_runs(session)
        correlation_logs = latest_correlation_results(session, analysis_status=None)

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
