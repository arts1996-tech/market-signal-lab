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
- virtual_accounts
- virtual_account_daily_states
- virtual_account_events

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

## 仮想口座台帳

`virtual_accounts`は短期・中期それぞれの通貨、初期資金、戦略版、状態版を保持する。`virtual_account_daily_states`は口座・JST営業日ごとに最初の現金、評価額、損益、最大ドローダウン、保有、翌日注文、不変シグナル履歴を固定する。`virtual_account_events`は判断、約定予定、約定、決済、見送り、日次残高を個別の追記イベントとして保持する。

- 同じ口座・営業日は一意とし、入力版と状態ハッシュが一致する再実行だけを冪等に扱う。
- 異なる後発入力で同じ営業日を置換しない。
- 3テーブルのUPDATE／DELETEはDB triggerで拒否する。訂正が必要な場合は、将来の明示的な訂正イベントとして設計し、既存行を変更しない。
- 再起動後は最新の日次状態とシグナル履歴を読み、決定論的な口座エンジンへ復元する。
- PostgreSQLを正本とする。JSONは日次状態とイベントを含む監査エクスポートであり、唯一の口座状態にしない。
- `0011_virtual_account_ledger`はMac側で往復検証済みだが、Raspberry Piへはユーザー承認前に適用しない。
