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
