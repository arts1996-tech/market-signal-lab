from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import streamlit as st

from app.database.repositories import latest_fetch_logs, latest_job_runs
from app.database.session import SessionLocal
from app.services.analysis_service import DEFAULT_SYMBOLS, load_market_analysis


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


st.title("Market Signal Lab")
st.caption("短期取引と中期投資の判断材料を整理する分析アプリです。自動売買や投資助言は行いません。")

tab_market, tab_correlation, tab_system = st.tabs(["市場ダッシュボード", "市場連動性", "システム管理"])

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
