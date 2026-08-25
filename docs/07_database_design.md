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
- asset_trading_capabilities
- margin_market_snapshots
- financing_term_snapshots
- technical_indicators
- spillover_features
- correlation_results
- regression_results
- analysis_results
- asset_analysis_runs
- asset_analysis_results
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
- user_asset_selections
- user_asset_selection_items
- themes
- theme_versions
- theme_asset_memberships
- theme_factor_definitions
- theme_liquidity_policies
- theme_analysis_runs
- theme_scores
- theme_rankings
- theme_events
- theme_tier_change_proposals

信用取引のテーブル名は実装時のマイグレーション設計で確定する。既存の`assets`や`positions`へ現在値だけを追加して履歴を失わず、適格性・残高・費用・保証金条件を時点付きスナップショットとして分離する。

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

`asset_analysis_runs`は全銘柄バッチの通常／デモ区分、入力版、ルール版、source方針、データ時点、検査対象数、品質ゲート通過数、状態を保持する。`asset_analysis_results`は実行IDと銘柄の組を一意にし、注目度・変動候補スコア、順位、観測数、業種、結果JSONを保持する。バッチ母集団へUI上限を適用せず、画面だけをページ読込する。

## 時刻
- DBはUTC
- 取引所タイムゾーンを別途保持
- 画面はJSTを基本に、必要に応じ現地時刻を併記

## 仮想口座台帳

`virtual_accounts`は短期・中期それぞれの通貨、初期資金、戦略版、状態版を保持する。`virtual_account_daily_states`は口座・判断系統・JST営業日ごとに最初の現金、評価額、損益、最大ドローダウン、保有、翌日注文、不変シグナル履歴を固定する。`virtual_account_events`は判断、約定予定、約定、決済、見送り、日次残高を個別の追記イベントとして保持する。

- `decision_track`は`delayed_historical`または`current_market`とし、同じ口座・系統・営業日を一意にする。
- 観察日時、価格最終日、東証営業日基準の遅延日数、取得元、観察入力ハッシュ、品質状態、理由コードを検索可能な列として保存する。
- 入力版と状態ハッシュが一致する再実行だけを冪等に扱う。
- 異なる後発入力で同じ営業日を置換しない。
- 3テーブルのUPDATE／DELETEはDB triggerで拒否する。訂正が必要な場合は、将来の明示的な訂正イベントとして設計し、既存行を変更しない。
- 再起動後は判断系統別に最新の日次状態とシグナル履歴を読み、決定論的な口座エンジンへ復元する。
- PostgreSQLを正本とする。JSONは日次状態とイベントを含む監査エクスポートであり、唯一の口座状態にしない。
- 実環境への適用状態は本書へ固定せず、Alembic revisionと運用確認で判定する。Raspberry Piへは利用者承認前に適用しない。

## 信用取引データ

詳細要件は[23 信用取引モード仕様](23_margin_trading.md)を参照する。

### `asset_trading_capabilities`候補

- asset_id
- market / broker_scope
- margin_long_eligible / margin_short_eligible
- credit_type: standardized / general / market_specific / not_applicable
- is_lending_issue / short_availability
- repayment_deadline
- restriction_status
- effective_from / effective_to / available_at / fetched_at
- source / source_version / data_quality_status

同一資産でも取引可否や在庫は変化するため、現在値で過去を上書きしない。米国資産へ日本固有の制度区分を設定せず、該当しない値はNULLまたは`not_applicable`とする。

### `margin_market_snapshots`候補

- asset_id / session_date
- margin_long_balance / margin_short_balance
- lending_ratio
- reverse_stock_borrow_fee
- effective_at / available_at / fetched_at
- source / quality_status

### `financing_term_snapshots`候補

- asset_id / market / broker_scope / currency
- margin_interest_rate / stock_lending_fee / borrow_cost
- initial_margin_rate / maintenance_margin_rate
- repayment_term_days / forced_liquidation_rule_version
- effective_from / effective_to / available_at / fetched_at
- source / quality_status

### 既存テーブルへの影響

- `trading_signals`、判断カード、バックテスト結果に`trade_mode`、適格性、却下理由、信用データ版、費用版、保証金版、リスク規則版を保存する。
- `positions`と`virtual_account_events`に売買方向、建玉総額、必要保証金、維持率、総レバレッジ、累積信用費用、返済期限、強制決済理由を保持できるようにする。
- `virtual_account_daily_states`に現物拘束額、信用必要保証金、利用可能余力、建玉総額、総レバレッジ、口座維持率を保持できるようにする。
- `backtest_runs`と`backtest_results`は現物、信用買い、信用売り、`auto_select`を別系列・別ルール版として識別する。
- `auto_select`では比較した全モードの入力ハッシュ、評価、却下理由を監査可能にする。

DB変更は追加マイグレーションで行い、既存の現物ポジションは`cash`へ安全に移行する。NULLをゼロや「取引可能」へ変換しない。Raspberry Pi適用前にMacでupgrade/downgrade、既存台帳の不変性、バックアップ・リストアを確認する。

## 利用者指定ティッカー集合

詳細は[24 利用者指定ティッカー分析・仮想口座仕様](24_user_selected_ticker_simulation.md)を参照する。

- `user_asset_selections`: 名称、版、作成日時、適用開始、作成者、状態、選定理由、構成ハッシュ
- `user_asset_selection_items`: selection_id、asset_id、追加日時、表示順、状態
- `analysis_runs`: selection_id、selection_version、asset_id、as_of、入力ハッシュ、分析版を関連付ける
- `backtest_runs`: `scope=selected_universe`、selection_id、selection_version、分析スナップショット集合ハッシュを保存する
- `virtual_accounts`: `account_scope=selected_universe`、allowed_selection_id、allowed_selection_versionを保存する

集合の追加・削除はUPDATEで過去構成を置換せず新版として保存する。既存シミュレーションと継続口座は開始時の集合版を参照し続ける。指定集合外の`asset_id`を仮想注文・ポジションへ保存しようとした場合はサービス境界で拒否する。

## シミュレーションレビューとナレッジ

詳細は[25 シミュレーション振り返り・ナレッジ更新仕様](25_simulation_knowledge_feedback.md)を参照する。

- `simulation_reviews`: 判断カード、口座イベント、取引結果、見送り結果、ベンチマーク、レビュー版を関連付ける。
- `knowledge_items`: 観測、仮説、検証中、検証済み、結論不十分、反証、廃止の状態、適用範囲、構造化条件、版を保持する。
- `knowledge_evidence_links`: 判断、取引、見送り、検証窓、分析結果、ニュース参照を賛成・反対証拠として関連付ける。
- `strategy_change_proposals`: 現行戦略版との差、検証証拠、承認状態、承認者、承認時刻を保持する。
- `knowledge_events`: 状態遷移と承認を追記型監査イベントとして保持する。

結果判明後も当初の入力、判断、仮説を更新しない。訂正、反証、廃止は新しいイベントとして保存する。ニュース本文は保存権限がある場合だけ保持し、それ以外は許可された識別子、URL、時刻、要約、構造化特徴に限定する。

## テーマ・セクターETF

詳細は[27 注目テーマ・セクターETF仕様](27_theme_sector_etf.md)を参照する。

- `themes`: 安定した識別子、名称、状態、created_at、updated_at
- `theme_versions`: 基準Tier、enabled、テーマ単位のmargin_trading_enabled、説明、適用期間、作成・承認時刻、構成ハッシュ
- `theme_asset_memberships`: テーマ版、asset_id、役割、重み、適用期間、出典
- `theme_factor_definitions`: 説明変数、方向、必須性、ラグ、変換、重み、モデル版
- `theme_liquidity_policies`: 投資期間・取引モード別の流動性規則版
- `theme_analysis_runs`: as_of、decision_track、入力版・ハッシュ、モデル版、品質状態
- `theme_scores`: 総合点、構成要素、coverage、根拠、反対材料
- `theme_rankings`: 実行時点の順位、同点規則、対象集合ハッシュ
- `theme_events`: ニュース・政策・地政学等の時点付き構造化参照
- `theme_tier_change_proposals`: Tier変更案、証拠、将来の適用開始、承認状態

`related_etfs`、`related_stocks`、`leading_us_assets`、`related_commodities`、`related_fx`を単一配列列へ固定せず、関連資産の役割と有効期間を正規化する。`analysis_model`はfactor定義とモデル版、`minimum_liquidity`は流動性規則へ分離する。テーマ単位の`margin_trading_enabled`だけで取引可能とせず、資産・市場・証券会社・時点別適格性を必須とする。テーマ定義、構成、Tier、スコア、流動性規則を現在値で過去へ上書きしない。遅延研究と現在判断を一意制約・検索条件で分離し、欠損要素を0として保存しない。
