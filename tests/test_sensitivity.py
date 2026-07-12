import pandas as pd

from app.analysis.sensitivity import sector_sensitivity


def test_sector_sensitivity_reports_observed_slope_and_sample_count():
    frame = pd.DataFrame(
        {
            "symbol": ["A", "A", "B", "B"],
            "sector": ["半導体"] * 4,
            "us_return": [0.01, -0.01, 0.02, -0.02],
            "target_return": [0.02, -0.02, 0.03, -0.03],
        }
    )

    result = sector_sensitivity(frame, min_samples=4)

    assert result["sector"].iloc[0]["sample_size"] == 4
    assert result["sector"].iloc[0]["symbol_count"] == 2
    assert result["sector"].iloc[0]["slope"] > 0


def test_sector_sensitivity_excludes_small_samples():
    frame = pd.DataFrame({"symbol": ["A"], "sector": ["金融"], "us_return": [0.01], "target_return": [0.02]})

    assert sector_sensitivity(frame, min_samples=2)["sector"].empty
