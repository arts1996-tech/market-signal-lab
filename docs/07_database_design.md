# 07 データベース設計

## PostgreSQL
時系列、分析結果、ジョブ、LLM、Slack会話を一元管理する。

## 主要テーブル候補
- assets
- exchanges
- trading_calendars
- market_sessions
- market_prices
- fx_rates
- interest_rates
- financial_results
- corporate_events
- etf_profiles
- technical_indicators
- spillover_features
- correlation_results
- regression_results
- analysis_results
- trading_signals
- backtest_runs
- backtest_results
- portfolios
- positions
- api_fetch_logs
- job_runs
- gemini_runs
- slack_conversations
- notifications

## assets主要項目
- id
- symbol
- name
- asset_type: jp_stock/us_stock/jp_etf/us_etf/market_index/fx/interest_rate/commodity
- exchange_id
- currency
- timezone
- benchmark_asset_id
- sector
- industry
- is_investable
- is_active
- metadata JSONB

## market_prices
- asset_id
- timeframe
- price_time UTC
- session_date
- open/high/low/close/adjusted_close
- volume
- source
- fetched_at
- data_quality_status

重複を防ぐ一意制約を設ける。

## 分析の再現性
分析結果に以下を保存する。
- analysis_type
- as_of_time
- input_data_version
- rule_version
- model_version
- parameters JSONB
- result JSONB
- created_at

## 時刻
- DBはUTC
- 取引所タイムゾーンを別途保持
- 画面はJSTを基本に、必要に応じ現地時刻を併記
