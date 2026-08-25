from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import streamlit as st

from app.services.lite_dashboard_service import (
    load_lite_market_overview,
    load_lite_virtual_accounts,
)


st.set_page_config(
    page_title="Market Signal Lite",
    page_icon=":compass:",
    layout="wide",
)


def format_date(value: date | datetime | None) -> str:
    if value is None:
        return "-"
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def format_yen(value, *, signed: bool = False) -> str:
    if value is None:
        return "評価保留"
    return f"¥{float(value):+,.0f}" if signed else f"¥{float(value):,.0f}"


@st.cache_data(ttl=300)
def load_overview() -> dict:
    return load_lite_market_overview()


@st.cache_data(ttl=60)
def load_accounts() -> dict:
    return load_lite_virtual_accounts()


st.title("Market Signal Lite")
st.caption("日米連動・市場テーマ・仮想口座を日常的に確認するための軽量画面です。")
st.warning(
    "現在は遅延データによる研究版です。現在の売買判断、投資助言、利益保証、"
    "証券会社への発注は行いません。"
)

active_page = st.radio(
    "表示",
    ["ホーム", "仮想口座", "利用範囲"],
    horizontal=True,
    label_visibility="collapsed",
)

if active_page == "ホーム":
    overview = load_overview()
    st.subheader("研究状態")
    if not overview["database_available"]:
        st.error(overview["current_market_reason"])
    else:
        status = overview["status"]
        columns = st.columns(4)
        columns[0].metric("モード", "デモ" if overview["mode"] == "demo" else "遅延研究")
        columns[1].metric("判断系列", overview["decision_track"])
        columns[2].metric("価格最終日", format_date(status.get("period_end")))
        columns[3].metric("品質警告", status.get("warning_count", 0))

        if overview["mode"] == "demo":
            st.warning("合成データだけを使用するデモモードです。投資判断には使用できません。")
        else:
            st.error(overview["current_market_reason"])

        warnings = overview.get("warnings", [])
        if warnings:
            with st.expander("データ品質警告", expanded=False):
                for warning in warnings:
                    st.write(f"- {warning.get('message', warning.get('status', '品質警告'))}")

    st.subheader("判断機能の利用可能性")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "機能": "日米波及の研究",
                    "状態": "Labで基礎実装済み・Lite接続準備中",
                    "注意": "相関は因果関係や利益を保証しません",
                },
                {
                    "機能": "金などのテーマ兆候",
                    "状態": "データソース・時点整合設計待ち",
                    "注意": "現在の上昇理由を後付けで同期間へ最適化しません",
                },
                {
                    "機能": "現在の投資判断",
                    "状態": "利用不可",
                    "注意": "現在価格と同時点ニュースが未接続です",
                },
                {
                    "機能": "短期・中期仮想口座",
                    "状態": "遅延研究系列のみ",
                    "注意": "正式な前向き運用期間には算入しません",
                },
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )

if active_page == "仮想口座":
    payload = load_accounts()
    st.subheader("短期・中期の仮想口座")
    st.caption(
        "表示対象は delayed_historical だけです。現在判断口座や実際の証券口座ではありません。"
    )
    if not payload["database_available"]:
        st.error("PostgreSQLへ接続できないため、仮想口座を確認できません。")
    else:
        for account in payload["accounts"]:
            st.markdown(f"### {account.get('label') or account['account_name']}")
            if not account["recorded"]:
                st.info("遅延研究系列の口座状態はまだ記録されていません。")
                continue
            columns = st.columns(4)
            columns[0].metric("評価額", format_yen(account["equity"]))
            columns[1].metric("現金", format_yen(account["cash"]))
            columns[2].metric("累積損益", format_yen(account["cumulative_pnl"], signed=True))
            columns[3].metric(
                "最大ドローダウン",
                "-"
                if account["maximum_drawdown"] is None
                else f"{account['maximum_drawdown']:.2%}",
            )
            st.caption(
                f"記録日: {format_date(account['session_date'])} / "
                f"価格最終日: {format_date(account['price_latest_session'])} / "
                f"遅延: {account['data_delay_days'] if account['data_delay_days'] is not None else '-'}営業日 / "
                f"保有: {account['position_count']} / 予定注文: {account['pending_order_count']}"
            )
            if account["quality_gate_status"] != "passed":
                reasons = " / ".join(account["quality_gate_reasons"]) or "品質ゲート未通過"
                st.warning(reasons)

        monitor = payload["monitor"]
        if monitor.get("warnings"):
            st.warning("運用警告: " + " / ".join(monitor["warnings"]))

if active_page == "利用範囲":
    st.subheader("このLite版で確認できること")
    st.markdown(
        """
- 保存済み市場データの時点と品質
- 遅延研究として記録した短期・中期仮想口座
- 現在利用できる機能と、利用できない理由
"""
    )
    st.subheader("まだ利用できないこと")
    st.markdown(
        """
- 最新価格と最新ニュースを使った今日の判断
- 金上昇等のテーマ兆候の正式判定
- 十分な未見・前向き実績に基づく予測能力の評価
- 信用取引、ナレッジ自動更新、LLM統合
- 証券会社への注文、注文予約、ブラウザ自動操作
"""
    )
    st.info("詳細分析、バックテスト、収集・監査・運用管理は既存のMarket Signal Labで確認します。")
