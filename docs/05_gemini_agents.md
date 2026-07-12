# 05 Geminiエージェント仕様

## 原則
Geminiは計算エンジンではなく、Pythonで計算済み・検証済みの構造化データを解釈し、複数の観点を統合して説明する。

## 初期エージェント
1. 日本市場分析エージェント
2. 米国市場分析エージェント
3. 日米市場波及分析エージェント
4. 株式・ETF分析エージェント
5. エントリー・イグジット判断エージェント
6. リスク・バックテスト検証エージェント
7. 投資判断統括エージェント

将来、日本株、米国株、日本ETF、米国ETFの専門エージェントへ分割可能にする。

## ツール候補
- get_market_summary
- get_us_to_japan_spillover
- screen_assets
- analyze_symbol
- get_entry_plan
- get_exit_plan
- get_position_risk
- get_similar_market_days
- run_backtest
- get_upcoming_earnings
- get_portfolio_summary
- get_system_status

## 入力
- 対象資産メタデータ
- データ時点
- 市場環境
- テクニカル・ファンダメンタル
- 相関・回帰
- 日米波及
- バックテスト
- 保有情報
- リスク・品質警告

## 構造化出力
JSON Schemaで最低限以下を要求する。
- decision
- short_term_view
- mid_term_view
- entry_condition
- entry_price_range
- take_profit_levels
- stop_loss
- invalidation_condition
- expected_holding_days
- risk_reward_ratio
- confidence
- supporting_factors
- risk_factors
- data_quality_warnings
- human_review_required

## ガードレール
- データにない価格・事実を作らない
- 古いデータを最新として扱わない
- 根拠と反対材料を併記
- サンプル数が少ない場合は信頼度を下げる
- 決算・イベント前は警告
- 強い断定や利益保証をしない
- 自動注文を要求・実行しない
- Gemini停止時はPython分析のみで縮退運転

## 監査
入力JSON、出力JSON、モデル名、プロンプト版、トークン、実行時間、エラー、作成時刻を保存する。
