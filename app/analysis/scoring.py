from dataclasses import dataclass


SHORT_TERM_RULE_VERSION = "short-term-v0.1"
MID_TERM_RULE_VERSION = "mid-term-v0.1"


@dataclass(frozen=True)
class ScoreResult:
    score: int
    label: str
    positive_reasons: list[str]
    negative_reasons: list[str]
    rule_version: str


def classify_short_term(score: int) -> str:
    if score >= 75:
        return "強気"
    if score >= 60:
        return "やや強気"
    if score >= 45:
        return "中立"
    if score >= 30:
        return "やや慎重"
    return "慎重"


def score_short_term(inputs: dict) -> ScoreResult:
    weights = {
        "trend": 25,
        "volume": 20,
        "overseas": 15,
        "technical": 15,
        "volatility": 10,
        "fx_rates": 5,
        "event_risk": 10,
    }
    score = 0
    positive: list[str] = []
    negative: list[str] = []
    for key, weight in weights.items():
        value = max(0.0, min(1.0, float(inputs.get(key, 0.5))))
        points = round(weight * value)
        score += points
        reason = f"{key}: {points}/{weight}"
        if value >= 0.55:
            positive.append(reason)
        elif value <= 0.45:
            negative.append(reason)
    score = max(0, min(100, score))
    return ScoreResult(score, classify_short_term(score), positive, negative, SHORT_TERM_RULE_VERSION)


def score_mid_term(inputs: dict) -> ScoreResult:
    weights = {
        "growth": 25,
        "trend": 20,
        "profitability": 15,
        "valuation": 15,
        "financial_health": 10,
        "relative_strength": 10,
        "risk": 5,
    }
    score = 0
    positive: list[str] = []
    negative: list[str] = []
    for key, weight in weights.items():
        value = max(0.0, min(1.0, float(inputs.get(key, 0.5))))
        points = round(weight * value)
        score += points
        reason = f"{key}: {points}/{weight}"
        if value >= 0.55:
            positive.append(reason)
        elif value <= 0.45:
            negative.append(reason)
    score = max(0, min(100, score))
    return ScoreResult(score, classify_short_term(score), positive, negative, MID_TERM_RULE_VERSION)

