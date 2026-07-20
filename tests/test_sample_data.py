from app.collectors.sample_data import generate_sample_market_data


def test_demo_data_includes_japanese_assets_with_sufficient_history():
    frame = generate_sample_market_data(periods=60)

    assert {"DEMOJP1", "DEMOJP2"}.issubset(set(frame["symbol"]))
    for symbol in ("DEMOJP1", "DEMOJP2"):
        sample = frame[frame["symbol"] == symbol]
        assert len(sample) == 60
        assert sample["source"].eq("sample").all()
        assert sample["price_basis"].eq("synthetic_close_only").all()
