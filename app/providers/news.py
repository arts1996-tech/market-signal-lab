from dataclasses import asdict, dataclass

import pandas as pd


@dataclass(frozen=True)
class NewsItem:
    published_at: pd.Timestamp
    symbol: str
    headline: str
    sentiment: float
    relevance: float
    source: str = "demo_scenario"
    synthetic: bool = True

    def as_record(self) -> dict:
        return asdict(self)


class DemoNewsProvider:
    """Deterministic news scenarios used only to exercise the demo pipeline."""

    _templates = (
        ("業績見通しを上方修正した想定ニュース", 0.8),
        ("新規受注の増加を発表した想定ニュース", 0.6),
        ("原材料コスト上昇を警告した想定ニュース", -0.7),
        ("事業計画は市場予想並みという想定ニュース", 0.1),
        ("製品不具合の調査開始という想定ニュース", -0.9),
    )

    def build(self, dates: pd.DatetimeIndex, symbols: list[str]) -> pd.DataFrame:
        if len(dates) < 30 or not symbols:
            return pd.DataFrame()
        records = []
        for symbol_index, symbol in enumerate(symbols):
            for event_index, offset in enumerate(range(24 + symbol_index * 3, len(dates), 22)):
                headline, sentiment = self._templates[(event_index + symbol_index) % len(self._templates)]
                records.append(
                    NewsItem(
                        published_at=pd.Timestamp(dates[offset]),
                        symbol=symbol,
                        headline=headline,
                        sentiment=sentiment,
                        relevance=0.9,
                    ).as_record()
                )
        return pd.DataFrame(records).sort_values("published_at").reset_index(drop=True)
