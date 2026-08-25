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
- trade_mode: cash / margin_long / margin_short / auto_select
- eligibility_status / rejected_mode_reasons
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
- margin_requirement / maintenance_ratio / gross_leverage（信用取引時）
- financing_costs / repayment_deadline / forced_liquidation_risk（信用取引時）

## ガードレール
- データにない価格・事実を作らない
- 古いデータを最新として扱わない
- 根拠と反対材料を併記
- サンプル数が少ない場合は信頼度を下げる
- 決算・イベント前は警告
- 強い断定や利益保証をしない
- 自動注文を要求・実行しない
- 信用可否、在庫、費用、保証金等が未確認のモードを取引可能として補完しない
- `auto_select`を自動売買へ変換せず、採用・却下理由を説明するだけにする
- 取引モード、数量、保証金、費用、維持率はPythonの検証済み計算結果だけを使用する
- Gemini停止時はPython分析のみで縮退運転

## 監査
入力JSON、出力JSON、モデル名、プロンプト版、トークン、実行時間、エラー、作成時刻を保存する。

## 任意の複数LLM検証

Gemini安定後、利用者が費用と対象モデルを承認した場合だけ、ClaudeやOpenAI等による独立検証を追加できる。

- 一次モデルの会話履歴や文体ではなく、同じ検証済み構造化入力と判断カードを渡す。
- データとの整合、反対材料、信頼度、データにない事実の混入を確認する。
- 不一致時は`human_review_required=true`とし、両論とモデル版を保存する。自動的に多数決で売買判断を確定しない。
- 同期実行を必須にせず、明示的な検証または非同期の後追い結果を選べるようにする。
- 入出力、モデル、プロンプト版、実行時刻、費用、エラーを一次判断と分離して監査する。
- 複数モデルの一致は、入力データの誤り、先読み、予測能力の正しさを保証しない。
- 料金、保存・学習利用、規約、モデル選定理由は導入時に公式情報で再確認する。
