from pathlib import Path


def test_dashboard_executes_only_the_selected_page():
    source = Path("app/dashboard/streamlit_app.py").read_text(encoding="utf-8")

    assert "st.tabs(" not in source
    assert "active_page = st.radio(" in source
    for page in [
        "市場ダッシュボード",
        "短期分析",
        "銘柄・ETF分析",
        "変動候補",
        "仮想投資評価",
        "市場連動性",
        "日米波及分析",
        "システム管理",
    ]:
        assert f'if active_page == "{page}":' in source


def test_demo_virtual_page_does_not_require_live_analysis_load():
    source = Path("app/dashboard/streamlit_app.py").read_text(encoding="utf-8")

    assert 'elif is_demo and active_page == "仮想投資評価":' in source
    assert 'if active_page in {"市場ダッシュボード", "市場連動性"}' in source


def test_fundamental_view_shows_provenance_and_formats_ratios_as_percentages():
    source = Path("app/dashboard/streamlit_app.py").read_text(encoding="utf-8")

    for label in [
        "取得元",
        "取得時刻（JST）",
        "開示時刻（JST）",
        "期間末",
        "通貨",
        "単位",
    ]:
        assert label in source
    assert 'elif key in {"roe", "operating_margin"}:' in source
    assert "display = format_percent(value)" in source
    assert 'display = f"{value:.2f}%"' not in source
    assert "render_fundamental_summary(screening)" in source


def test_asset_screening_reads_persisted_paged_batch_results():
    source = Path("app/dashboard/streamlit_app.py").read_text(encoding="utf-8")

    assert "load_asset_analysis_page(" in source
    assert '"1ページの表示件数", [25, 50, 100, 200]' in source
    assert "バッチは品質ゲート通過全銘柄を対象" in source
    assert "load_asset_screening_analysis(session)" not in source

    analysis_source = Path("app/services/analysis_service.py").read_text(
        encoding="utf-8"
    )
    assert ".head(10)" not in analysis_source
    assert "limit: int = 200" not in analysis_source


def test_system_page_discloses_segment_sample_gate():
    source = Path("app/dashboard/streamlit_app.py").read_text(encoding="utf-8")

    assert "実データ検証の相場局面・属性別評価" in source
    assert "30件未満の区分は成績判定せず" in source
    assert 'job_name="run_backtest"' in source
