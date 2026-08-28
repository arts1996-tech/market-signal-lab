from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import streamlit as st

from app.services.lite_dashboard_service import (
    create_lite_analysis_snapshot,
    create_lite_selection_version,
    deactivate_lite_selection,
    load_lite_market_overview,
    load_lite_research_results,
    load_lite_selections,
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


@st.cache_data(ttl=60)
def load_research() -> dict:
    return load_lite_research_results()


@st.cache_data(ttl=60)
def load_selections() -> dict:
    return load_lite_selections()


def show_operation_result(result: dict, success_message: str) -> None:
    if result.get("ok"):
        st.success(success_message)
        load_selections.clear()
        return
    for error in result.get("errors", [{"reason": "操作に失敗しました"}]):
        prefix = f"{error['ticker']}: " if error.get("ticker") else ""
        st.error(prefix + str(error.get("reason", "入力を確認してください")))


st.title("Market Signal Lite")
st.caption("日米連動・市場テーマ・仮想口座を日常的に確認するための軽量画面です。")
st.warning(
    "現在は遅延データによる研究版です。現在の売買判断、投資助言、利益保証、"
    "証券会社への発注は行いません。"
)

active_page = st.radio(
    "表示",
    ["ホーム", "判断・日米波及", "指定ティッカー", "仮想口座", "利用範囲"],
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
                    "状態": "保存済み結果をLiteで確認可能",
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

if active_page == "判断・日米波及":
    payload = load_research()
    st.subheader("保存済み判断")
    st.caption(
        "画面表示では新しい判断を計算しません。delayed_historicalとしてDBへ保存済みの記録だけを表示します。"
    )
    if not payload["database_available"]:
        st.error("PostgreSQLへ接続できないため、保存済み研究結果を確認できません。")
    else:
        decisions = payload["decisions"]
        if not decisions:
            st.info("保存済みの判断記録はまだありません。")
        else:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "判断時刻": row["event_at"],
                            "銘柄": row["symbol"],
                            "名称": row["name"],
                            "判断": row["decision"],
                            "状態": row["status"],
                            "スコア": row["score"],
                            "方向": row["direction"],
                            "理由コード": row["reason_code"],
                            "口座": " / ".join(row["account_labels"]),
                            "データ時点": row["data_as_of"],
                        }
                        for row in decisions
                    ]
                ),
                hide_index=True,
                use_container_width=True,
            )
            with st.expander("根拠・反対材料・品質警告", expanded=False):
                for row in decisions:
                    st.markdown(f"**{row['symbol'] or '-'} / {row['decision']}**")
                    st.write("根拠: " + (" / ".join(row["reasons"]) or "記録なし"))
                    st.write("反対材料: " + (" / ".join(row["counterarguments"]) or "記録なし"))
                    if row["quality_warnings"]:
                        st.warning(" / ".join(row["quality_warnings"]))

        st.subheader("保存済み日米波及")
        st.caption("統計的な関連であり、因果関係や利益を保証するものではありません。")
        spillovers = payload["spillovers"]
        if not spillovers:
            st.info("保存済みの日米波及モデル結果はまだありません。")
        else:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "米国側": row["base_symbol"],
                            "日本側": row["target_symbol"],
                            "目的変数": row["target_metric"],
                            "窓": row["window_days"],
                            "標本数": row["sample_size"],
                            "米国リターン係数": row["coefficient"],
                            "p値": row["p_value"],
                            "決定係数": row["r_squared"],
                            "期間末": row["period_end"],
                            "モデル版": row["model_version"],
                        }
                        for row in spillovers
                    ]
                ),
                hide_index=True,
                use_container_width=True,
            )

if active_page == "指定ティッカー":
    payload = load_selections()
    st.subheader("指定ティッカー集合")
    st.caption(
        "集合の版と分析を分離して保存します。表示や再読み込みだけでは分析・シミュレーション・仮想口座を開始しません。"
    )
    if not payload["database_available"]:
        st.error("PostgreSQLへ接続できないため、指定集合を確認できません。")
    else:
        selections = payload["selections"]
        if not selections:
            st.info("指定ティッカー集合はまだありません。下のフォームから作成できます。")
        for selection in selections:
            status_label = "有効" if selection["status"] == "active" else "無効"
            st.markdown(
                f"### {selection['name']} — v{selection['version']}（{status_label}）"
            )
            st.caption(
                f"適用開始: {format_date(selection['effective_from'])} / "
                f"版数: {selection['version_count']} / 構成ハッシュ: {selection['composition_hash'][:12]}…"
            )
            st.dataframe(
                pd.DataFrame(selection["items"])[
                    ["display_order", "symbol", "name", "exchange", "asset_type", "currency"]
                ].rename(
                    columns={
                        "display_order": "順序",
                        "symbol": "ティッカー",
                        "name": "名称",
                        "exchange": "取引所",
                        "asset_type": "種別",
                        "currency": "通貨",
                    }
                ),
                hide_index=True,
                use_container_width=True,
            )
            analysis = selection["latest_analysis"]
            if analysis is None:
                st.info("この版の分析スナップショットはまだありません。")
            else:
                st.caption(
                    f"分析: {analysis['status']} / データ時点: {format_date(analysis['data_as_of'])} / "
                    f"ルール版: {analysis['rule_version']}"
                )
                if analysis["results"]:
                    st.dataframe(
                        pd.DataFrame(
                            [
                                {
                                    "ティッカー": row["symbol"],
                                    "状態": row["analysis_status"],
                                    "観測数": row["observations"],
                                    "品質理由": " / ".join(row["quality_reasons"]),
                                    "根拠": " / ".join(row["positive_reasons"]),
                                    "反対材料": " / ".join(row["negative_reasons"]),
                                }
                                for row in analysis["results"]
                            ]
                        ),
                        hide_index=True,
                        use_container_width=True,
                    )
            if selection["status"] == "active":
                confirm = st.checkbox(
                    "この集合を無効化することを確認",
                    key=f"confirm-deactivate-{selection['selection_id']}",
                )
                if st.button(
                    "無効版を追記",
                    key=f"deactivate-{selection['selection_id']}",
                    disabled=not confirm,
                ):
                    show_operation_result(
                        deactivate_lite_selection(selection_id=selection["selection_id"]),
                        "既存履歴を残して無効版を追記しました。",
                    )

        st.divider()
        st.subheader("集合の新規作成・新版作成")
        st.caption(
            "1行に market,exchange,symbol を入力します。例: jp,JPX,13060。"
            "銘柄の追加・除外・並べ替えは既存集合を更新せず、新しい版として保存します。"
        )
        selection_options = {"新しい集合": None}
        selection_options.update(
            {
                f"{row['name']}（現在v{row['version']}）": row["selection_key"]
                for row in selections
            }
        )
        with st.form("selection-version-form"):
            target_label = st.selectbox("保存先", list(selection_options))
            selection_name = st.text_input("集合名", max_chars=100)
            ticker_lines = st.text_area(
                "ティッカー",
                placeholder="jp,JPX,13060\nus,NASDAQ,NVDA",
                height=140,
            )
            rationale = st.text_area("目的・変更理由", max_chars=1000)
            submitted = st.form_submit_button("内容を検証して版を保存")
        if submitted:
            show_operation_result(
                create_lite_selection_version(
                    name=selection_name,
                    ticker_lines=ticker_lines,
                    rationale=rationale,
                    selection_key=selection_options[target_label],
                ),
                "指定ティッカー集合の版を保存しました。",
            )

        st.subheader("保存済み全銘柄分析からスナップショット作成")
        active_selections = [row for row in selections if row["status"] == "active"]
        source_runs = payload["source_runs"]
        if not active_selections or not source_runs:
            st.info("有効な指定集合と保存済み全銘柄分析の両方が必要です。")
        else:
            selection_labels = {
                f"{row['name']} v{row['version']}": row["selection_id"]
                for row in active_selections
            }
            run_labels = {
                f"{format_date(row['data_as_of'])} / {row['data_scope']} / {row['rule_version']}": row["run_id"]
                for row in source_runs
            }
            chosen_selection = st.selectbox("対象集合", list(selection_labels), key="analysis-selection")
            chosen_run = st.selectbox("元の保存済み分析", list(run_labels), key="analysis-source")
            st.caption("この操作は保存済み分析を指定集合へ固定します。仮想取引は開始しません。")
            if st.button("分析スナップショットを明示作成"):
                show_operation_result(
                    create_lite_analysis_snapshot(
                        selection_id=selection_labels[chosen_selection],
                        source_asset_analysis_run_id=run_labels[chosen_run],
                    ),
                    "指定集合の分析スナップショットを保存しました。",
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
- 保存済み日米波及と遅延研究の判断記録
- 指定ティッカー集合の版管理と保存済み分析の明示スナップショット
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
