from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app.database.repositories import latest_correlation_results, latest_fetch_logs, latest_job_runs
from app.database.session import SessionLocal
from app.services.analysis_service import DEFAULT_SYMBOLS, load_market_analysis, load_short_term_analysis


JST = ZoneInfo("Asia/Tokyo")


st.set_page_config(page_title="Market Signal Lab", page_icon=":chart_with_upwards_trend:", layout="wide")


def format_percent(value) -> str:
    if pd.isna(value):
        return "-"
    return f"{value:.2%}"


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


st.title("Market Signal Lab")
st.caption("短期取引と中期投資の判断材料を整理する分析アプリです。自動売買や投資助言は行いません。")

tab_market, tab_short, tab_correlation, tab_system = st.tabs(
    ["市場ダッシュボード", "短期分析", "市場連動性", "システム管理"]
)

analysis = load_data()
wide = analysis["wide"]
normalized = analysis["normalized"]
prices = analysis["prices"]

with tab_market:
    if wide.empty:
        st.warning("まだ価格データがありません。`python jobs/seed_sample_data.py` を実行してください。")
    else:
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

        st.info("欠損日は前方補完せず、取得できた観測値だけで計算しています。相関は因果関係を示すものではありません。")

        source_view = (
            prices.sort_values("fetched_at")
            .groupby("symbol")
            .tail(1)[["symbol", "source", "fetched_at"]]
            .assign(fetched_at_jst=lambda df: df["fetched_at"].map(format_jst))
        )
        st.dataframe(source_view[["symbol", "source", "fetched_at_jst"]], use_container_width=True)

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

with tab_system:
    with SessionLocal() as session:
        fetch_logs = latest_fetch_logs(session)
        job_runs = latest_job_runs(session)
        correlation_logs = latest_correlation_results(session)

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
            }
            for row in correlation_logs
        ],
        use_container_width=True,
    )
