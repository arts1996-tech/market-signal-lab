# Market Signal Lab

日本株、米国株、日本ETF、米国ETFを対象に、市場環境、銘柄選定、エントリー、利益確定、損切り、ポジションリスクの判断材料を整理する投資判断支援システムです。短期は1〜20営業日、中期は1か月〜1年程度を想定します。

日経平均、TOPIX、米国主要指数、VIX、USD/JPY、金利等は市場環境や円建て評価の参考データとして扱います。FX取引そのものは投資対象にしません。

このシステムは自動売買や証券会社への発注を行いません。「必ず上がる」「買うべき」といった断定や利益保証をせず、統計的な傾向、根拠、反対材料、過去検証、データ品質を提示します。最終的な投資判断と注文は利用者が行います。

> **現在の利用上の重要事項:** 現時点の仮想口座と候補スコアは、実際の売買判断や投資額決定の主な根拠にできる成熟度には達していません。正直な評価、利用してよい範囲、実投資支援前の必須品質ゲートは [docs/19_investment_decision_readiness_assessment.md](docs/19_investment_decision_readiness_assessment.md) を参照してください。

> **現行ToDoの正本:** 今後の最優先事項、実装順序、未完了項目、完了条件は [docs/22_current_priority_todo.md](docs/22_current_priority_todo.md) に一本化しています。過去文書に残る「次の作業」より、この文書を優先します。`NOW-P0-1`〜`NOW-P0-5`と`NOW-P1-1`〜`NOW-P1-8`の実装は完了しました。前向き口座の最初の実営業日を監視しながら、次は税モデルの扱いを明確化する`NOW-P1-9`です。

GitHub: https://github.com/arts1996-tech/market-signal-lab

## 仕様書と読む順序

実装・変更前に、ルートの `AGENTS.md` と対象フェーズの仕様書を確認してください。新しい製品要件は既存機能を後退させるものではなく、今後の開発順序と品質基準を定めるものです。

1. `AGENTS.md`
2. `docs/01_product_vision.md`
3. `docs/02_system_architecture.md`
4. `docs/03_investment_analysis.md`
5. `docs/04_us_japan_spillover.md`
6. `docs/05_gemini_agents.md`
7. `docs/06_slack_integration.md`
8. `docs/07_database_design.md`
9. `docs/08_raspberry_pi_operations.md`
10. `docs/09_security_and_quality.md`
11. `docs/10_development_roadmap.md`
12. `docs/11_api_and_data_sources.md`
13. `docs/12_acceptance_criteria.md`
14. `docs/13_review_and_recommendations.md`
15. `docs/14_dev_ops_environment_review.md`
16. `docs/15_cross_model_verification.md`
17. `docs/16_slack_free_plan_review.md`
18. `docs/17_remediation_todo.md`（完了済み是正履歴）
19. `docs/18_phase3_data_source_design.md`
20. `docs/19_investment_decision_readiness_assessment.md`
21. `docs/20_virtual_account_decision_logic.md`
22. `docs/21_forward_shadow_operations.md`
23. `docs/22_current_priority_todo.md`（現行ToDoの正本）

レビュー文書とオプション提案は、必須要件と区別して扱います。文書、既存コード、実運用状態に矛盾がある場合は、機能やルールを勝手に削除せず、差分と推奨案を確認してから変更します。

## 開発方針

本システムは原則として無料で利用できる技術、サービス、API、ライブラリで開発します。有料サービス、有料API、有料プランが必要な機能、無料枠の条件が不明なもの、将来的な課金リスクを判断しきれないものは、導入前に必ず確認します。

データソースの採用方針は [docs/data_sources.md](/Users/tsurusumu/Projects/market-signal-lab/docs/data_sources.md) にまとめます。

## 現在の基盤と機能

- Python 3.12 / Streamlit / PostgreSQL / SQLAlchemy / Alembic
- Docker ComposeによるApple Silicon MacとARM64のRaspberry Pi向け起動構成
- 資産マスターと日次価格テーブル
- FRED APIクライアント
- NASDAQ Composite、Dow Jones、S&P 500、日経平均、USD/JPYの保存
- 実データと隔離した明示的なデモ用サンプルデータ投入
- 日次リターン、米国前営業日と日本当日の対応
- 20日、60日、120日、250日相関
- 60日ローリング相関
- 米国株指数と日本株指数の相関結果をDBへ蓄積し、後続分析で再利用できる構成
- 米国前営業日の終値リターンと日本株・ETF当日の実OHLCを対応させた、寄り付きギャップ・場中・日次の波及分析、ラグ回帰、ローリング回帰、Granger検定の基盤
- J-Quantsの業種メタデータと観測済み波及データを使った、少数標本を除外する業種・銘柄感応度集計
- 短期分析タブで移動平均、EMA、RSI、MACD、ボリンジャーバンド、簡易短期スコアを表示
- 米国指数と日本指数の相関、個別銘柄の短期指標、スコアへ反映しない仮想投資参考統計を表示する変動候補タブ
- 実注文を行わない仮想投資評価タブ。候補に出した理由、損益、結果理由を表示
- 短期・中期それぞれ250万円から開始し、手数料・スプレッド・利益確定・損切り・残高推移を確認できるDB非保存のデモ仮想口座
- 実データ評価用の、次営業日始値約定・買いのみ・資金拘束・同時保有上限・手数料・スプレッド・税率を扱うイベント順仮想口座
- 判断時刻以前に利用可能だった価格だけから、買い候補・待機・データ不足を生成する時点整合シグナル境界（将来結果を使う過去評価とは別経路）
- 通常モード用の短期・中期各250万円の独立した継続口座状態。口座別の戦略版、利益確定、損切り、最大保有期間、現金、保有、損益、最大ドローダウン、翌日注文を分離
- 仮想口座の総損益、最大ドローダウン、勝率、平均取引損益、それらの95%近似区間、日経平均の同期間騰落率との差を計算する検証指標
- デモ・実データ評価共通のOHLCイベントエンジン、売買代金別の保守的コスト、流動性・部分約定・値幅制限／特別気配フラグ・業種／保有相関集中リスク・停止規則
- 株式分割・併合、配当落ち、権利日、現金配当支払日を時系列で反映する企業行動モデル。合併・株式交換、端株現金化、外貨配当は推測せず評価保留
- 企業行動イベントと確認済み期間を別々に保存し、未確認期間を警告または新規取引拒否できる保守的ゲート
- 許容損失額と損切り幅から逆算する参考数量、全保有の計画損失上限
- 戦略版・約定版・入力ハッシュを含む監査ID、判断カード、未見期間ウォークフォワード、Git対象外の遅延価格研究スナップショットJSON
- 指数比較チャート、相関グラフ、取得ログ、ジョブ履歴
- pytestによる主要ロジックのテスト
- 画面上部の通常／デモ、データ時点、取得元、価格基準、対象期間、品質警告の共通表示
- 銘柄・ETFの技術指標スクリーニング基盤（少数履歴は除外、財務値は推測しない）
- 選択した画面だけを実行するナビゲーション。非表示画面の分析やDB取得は自動実行しない

## 段階的な開発方針

開発は [docs/10_development_roadmap.md](/Users/tsurusumu/Projects/market-signal-lab/docs/10_development_roadmap.md) に従い、一度に全機能を実装しません。各フェーズ終了時にアプリを起動可能な状態に保ち、既存の先行実装も削除・無効化せず、テストで保護します。

1. 基盤・市場比較
2. 日米波及分析
3. 日本株・米国株・日本ETF・米国ETF分析
4. 売買計画とバックテスト
5. Geminiによる計算済みデータの解釈
6. Slackによる対話と通知
7. 保有管理と通知
8. Raspberry Pi本番運用の完成

GeminiとSlackは初期基盤には含めず、該当フェーズで料金、無料枠、利用規約、セキュリティを再確認し、導入承認後に実装します。Geminiには価格や指標を計算させず、Pythonで計算・検証済みの構造化データだけを渡します。Gemini停止時もPython分析とStreamlitで利用できる構成を維持します。

## ディレクトリ構成

```text
app/
  analysis/       分析ロジック
  collectors/     外部APIクライアント
  core/           設定、ログ、例外
  dashboard/      Streamlit画面
  database/       SQLAlchemyモデル、Repository、Alembic
  services/       収集・分析サービス
jobs/             定期実行できる独立コマンド
tests/            pytest
docker/           cron例
```

## アーキテクチャ方針

```text
External APIs
  -> Collectors / Providers
  -> PostgreSQL
  -> Python Analysis / Backtest
  -> Streamlit
  -> Gemini Agents（フェーズ5以降）
  -> Slack（フェーズ6以降）
```

- 外部API固有処理、DB処理、分析ロジック、サービス、UIを分離します。
- 数値計算とバックテストはPythonで決定論的に実行します。
- Streamlitは数値、チャート、分析履歴、データ品質を確認する画面です。
- Slackは将来の対話・通知窓口であり、Streamlitを置き換えません。
- DB時刻はUTC、画面表示は日本時間を基本とします。
- 取得処理は冪等にし、取得元、取得時刻、鮮度、欠損、品質警告を記録します。
- 分析ルール、入力データ、モデル、プロンプトをバージョン管理し、結果を再現可能にします。

## 分析データsourceの方針

通常分析は `source_priority_v1` を適用し、指数・為替はFRED、日本株・日本ETFはJ-Quantsの日次データだけを使用します。未登録のsourceへの暗黙フォールバックは行いません。分析結果には、選択済み入力のハッシュ、source方針、対象期間、品質状態、暫定の価格基準を保存・表示します。

入力sourceを復元できない過去の結果は `requires_recalculation` として残し、判断材料には使いません。価格基準（分割調整など）はP0-3で確定するまで `provider_reported_pending_p0_3` と明示します。

## MacBookでの起動

1. 環境ファイルを用意します。

```bash
cp .env.example .env
```

2. FRED APIキーを使う場合は `.env` の `FRED_API_KEY` に設定します。未設定でもサンプルデータで画面確認できます。

J-Quants Free planを使う場合は `.env` の `JQUANTS_API_KEY` に設定します。J-Quants Free planの株価は12週間遅延です。

米国株のSEC財務データを検証する場合は、`.env` の `SEC_USER_AGENT` にアプリ名と連絡先を設定してください。SECのFair Access要件に従うため、未設定のままSEC APIへ接続することはできません。現時点ではSEC APIの自動収集は未導入で、明示実行の単銘柄ジョブだけを提供しています。

J-Quants財務データを明示的に1銘柄取得する場合は、対象が既存のJ-Quants株・ETFであることを確認して実行します。現行のJ-Quants公開プラン表ではFreeの財務情報は対象外表示のため、Free契約での全銘柄自動収集は行いません。

```bash
docker compose run --rm app python jobs/collect_jquants_financial_summary.py \
  --code 86970 --from-date 2025-01-01 --to-date 2026-03-31
```

SEC財務データを明示的に1銘柄取得する場合は、User-Agentを設定したうえで次を実行します。対象銘柄が既存のUSD建て株・ETFで、`assets.sec_cik`が指定CIKと完全一致する場合だけAPIへ接続し、別企業への誤保存を防ぎます。

```bash
docker compose run --rm app python jobs/collect_sec_fundamentals.py --cik 0000320193 --symbol AAPL
```

CIKの資産マスター登録は、SEC公式の銘柄一覧JSONとUSD資産を明示指定して実行します。日本株・日本ETF、既存CIKとの重複はジョブが拒否します。

```bash
docker compose run --rm app python jobs/map_sec_cik.py \
  --json /path/to/company_tickers_exchange.json \
  --symbol AAPL
```

米国株を資産マスターへ追加する場合も、対象を明示した許可リストだけを処理します。ETFの自動判定や全銘柄一括登録は行いません。

```bash
docker compose run --rm app python jobs/import_sec_assets.py \
  --json /path/to/company_tickers_exchange.json \
  --symbols AAPL,MSFT
```

取得元と利用条件を人が確認したETF指標JSONは、次の明示実行だけで保存します。ETFでない銘柄、未登録銘柄、壊れたJSON、無効なsourceは拒否します。自動取得元はまだ採用していません。

```bash
docker compose run --rm app python jobs/save_etf_metrics.py \
  --file /path/to/reviewed-etf-metrics.json \
  --source provider_reviewed
```

3ジョブは共通サービスを使い、入力検証、Repository保存、冪等再実行、`api_fetch_logs`、`job_runs`、失敗分類を同じ形式で扱います。出力の`classification`は、新規保存`new_rows_saved`、同一入力の再実行`idempotent_replay`、有効行なし`no_valid_rows`、入力・対象・外部API・DB障害の個別理由を示します。エラー時は終了コード1となり、秘密を含み得るレスポンス本文は監査ログへ保存しません。

3. 初回起動またはマイグレーション変更時は、バックアップ確認後にDBマイグレーションを明示実行します。

```bash
docker compose run --rm app alembic upgrade head
```

4. Docker Composeで起動します。

```bash
docker compose up --build db app
```

5. ブラウザで開きます。

```text
http://localhost:8501
```

ラズパイの常駐収集が稼働中は、同じJ-Quants APIキーのレート制限競合を避けるため、Macで`jquants-collector`を同時起動しません。

「システム管理」タブでは、現在の収集段階、対象日、連続30営業日到達銘柄、直近30営業日のカバー率、残り要求上限、15秒間隔に基づく理論最短時間を確認できます。表示は接続中DBの状態であり、`localhost`はMacへ最後に複製した時点、ラズパイ画面はラズパイの実運用値です。

Compose起動時にはAlembicマイグレーションを自動適用しません。初回起動またはマイグレーション変更時だけ、上記の明示手順をバックアップ確認後に実行します。通常モードではサンプルデータを投入せず、実データだけを分析対象にします。価格がない場合はFREDまたはJ-Quantsの収集ジョブを実行してください。

### デモ用サンプルデータ

合成データは実データと混ぜません。デモとして使う場合だけ、`MARKET_DATA_MODE=demo` を設定してから明示的に投入します。

```bash
MARKET_DATA_MODE=demo docker compose run --rm app python jobs/seed_sample_data.py --demo
MARKET_DATA_MODE=demo docker compose up --build db app
# DBを使わず、合成データだけでフェーズ4仮想口座を確認する
MARKET_DATA_MODE=demo docker compose run --rm app python jobs/run_backtest.py --demo
# 取引台帳をMacのlogs/へ保存する場合（DBには保存しない）
MARKET_DATA_MODE=demo docker compose run --rm app python jobs/run_backtest.py --demo --ledger-path /app/logs/demo_virtual_ledger.json
# 未見期間を口座別に確保してから評価する（同じ口座で変更後ルールによる期間再利用を拒否）
MARKET_DATA_MODE=demo docker compose run --rm app python jobs/run_backtest.py --demo --validation-registry-path /app/data/validation/windows.json
# 現時点のデモ結果を前向き観察JSONとして保存する（DB・実注文には書き込まない）
MARKET_DATA_MODE=demo docker compose run --rm app python jobs/run_forward_shadow.py --demo
# JSTの東証営業日ごとに最初の結果だけを不変保存する
MARKET_DATA_MODE=demo docker compose run --rm app python jobs/run_forward_shadow.py --demo --daily
```

デモモードの画面は合成データだけを表示し、投資判断には使用できません。既存DB内のサンプル行も通常モードの分析・画面からは除外されます。

画面だけで確認する場合は、デモモードの「仮想投資評価」画面で「短期・中期のデモ仮想口座を実行」ボタンを押します。デモモードではこの画面を初期表示し、DBを参照せず決定論的な合成価格・合成ニュースだけで計算します。

短期・中期はそれぞれ250万円の独立口座です。暫定仮定は、判断日の次の東証営業日始値で約定、手数料0.10%、税率0%、100株単位、同時保有2銘柄まで、1銘柄30%までです。スプレッドと基礎スリッページは前営業日売買代金を10億円以上・2.5億円以上・5千万円以上の3段階に分けた保守的な代理値で、実際の板情報ではありません。5千万円未満は見送ります。1取引の計画損失を初期資金の1%、全保有の計画損失合計を5%以内とし、損切り幅から参考数量を逆算します。前営業日出来高の10%を参加率上限とし、部分約定を許可します。同一日足で利益確定と損切りへ同時到達した場合は損切りを優先します。現金、評価額、実現／未実現損益、最大ドローダウン、残高推移、保有、取引理由、判断カードの時系列、監査ID、複数ベンチマーク、未見期間別の検証を表示します。合成データの結果であり、実績・予測・投資判断には使用できません。

停止:

```bash
docker compose down
```

稼働状況とログの確認:

```bash
docker compose ps
docker compose logs -f app
docker compose logs -f db
```

フェーズ1の基本確認では、`http://localhost:8501` を開き、次を確認します。

- 市場ダッシュボードに5系列の比較チャートが表示される。
- 各系列のデータ時点、取得元、取得時刻、品質状態が表示される。
- 古い価格がある場合は警告が表示される。
- 市場連動性タブに20日、60日、120日、250日相関と60日ローリング相関が表示される。
- J-Quantsの日次OHLCを複数日取得済みの場合、「日米波及分析」タブで米国前営業日と日本当日の寄り付きギャップ・場中・日次リターンを確認できる。
- 欠損を前方補完しないこと、相関が因果関係を示さないことが明記される。

## 市場データ取得

FRED APIキーを設定したあと、以下を実行するとDBに実データを保存します。取得済みデータは一意制約で重複登録を防ぎます。

```bash
docker compose exec app python jobs/collect_us_market.py
```

Raspberry Piのディスク、価格件数、最終取得時刻、直近24時間の失敗／再試行ジョブを読み取り専用で確認するには、次を実行します。

```bash
docker compose exec app python jobs/check_operations.py
```

J-Quantsの期間取得方式を小規模に実測する場合は、DBへ書き込まない専用コマンドを使います。Free planのレート制限を守るため、短い期間・1銘柄で確認してください。

```bash
docker compose run --rm app python jobs/measure_jquants_period.py --code 86970 --from-date 20260401 --to-date 20260410
```

出力には取得行数、レイテンシ、測定時刻が含まれます。全銘柄取得の通常運用は、最新取得可能日を優先した常駐コレクターを使用します。

相関分析結果をDBへ蓄積する場合は、以下を実行します。米国前営業日リターンと日本当日リターンの対応で、ペア別相関、米国指数群と日本指数群の平均リターン同士の相関、ペア別相関の平均サマリーを `correlation_results` に保存します。

```bash
docker compose exec app python jobs/run_short_term_analysis.py
```

日米波及分析の観測値をDBへ保存するには、対象の日本株またはETFについて複数日のJ-Quants日次OHLCを取得してから、以下を実行します。

```bash
docker compose exec app python jobs/run_spillover_analysis.py --jp-symbol 13060
```

このジョブは、米国側にはFREDの前営業日終値リターン、日本側にはJ-Quantsの実際の始値・終値のみを利用し、観測値を `spillover_features`、ラグ回帰・ローリング回帰・Granger検定の結果を `spillover_model_results` に保存します。始値や終値が欠損した日は補完・推測せず、分析対象から外します。回帰は統計的な関連を確認するもので、因果関係や将来の値動きを保証しません。各回帰は少なくとも10件、Granger検定は30件の対応セッションが必要です。

個別の入口は以下です。

```bash
docker compose exec app python jobs/collect_japan_market.py
docker compose exec app python jobs/collect_fx.py
docker compose exec app python jobs/collect_jquants_listed_info.py --limit 20
docker compose exec app python jobs/collect_jquants_daily.py --code 86970 --date 20260401 --name "JPX" --asset-type stock
docker compose exec app python jobs/collect_jquants_daily_batch.py --date 20260401 --limit 3
docker compose exec app python jobs/run_mid_term_analysis.py
docker compose exec app python jobs/run_asset_analysis.py
docker compose exec app python jobs/run_backtest.py
```

`run_mid_term_analysis.py`は、分析時刻までに開示された財務だけを選び、売上・営業利益・EPS成長、営業利益率、ROE、自己資本比率、営業CFを計算します。価格が連続して63・126・252営業日そろう場合だけ3・6・12か月モメンタムと52週高値乖離を計算し、不足、古さ、通貨・単位不明を警告します。利用可能な指標がない場合は`success`にせず`insufficient_data`として`job_runs`へ保存します。通常モードの`run_backtest.py`も実データ用ウォークフォワード検証へ接続済みです。デモバックテストは別途、明示したデモモードで実行できます。

Streamlitの「銘柄・ETF分析」では、テクニカルの30営業日品質ゲートが未達でも、保存済み財務があれば財務欄を確認できます。履歴と最新スナップショットには取得元、取得時刻、開示時刻、期間末、通貨、単位を表示します。PBRは提供元から`book_value_per_share`を取得できた場合だけ計算し、自己資本から推定しません。ROEと営業利益率は百分率で表示し、未取得値は`-`のままです。

`run_asset_analysis.py`は、調整済み価格が30営業日以上ある全銘柄をDBから件数上限なしで読み、東証カレンダー上の最新連続30営業日を満たす全銘柄へ技術注目度と変動候補スコアを計算します。実行単位と銘柄別結果は`asset_analysis_runs`／`asset_analysis_results`へ入力版、分析ルール版、source方針、通常／デモ区分とともに保存します。同じ入力版の再実行は既存結果を再利用します。

「銘柄・ETF分析」は保存済みの最新通常データ結果だけを読み、銘柄区分、最低注目度、業種、銘柄コード・名称で絞り込めます。1ページは25／50／100／200件から選択でき、200件は表示上限であって分析母集団の上限ではありません。Mac復元DBの2026-08-16確認ではSQL上の対象3,047銘柄を全件検査し、最新連続30営業日を満たす銘柄は0件だったため`insufficient_data`です。データ収集後にジョブを再実行すると、新しい入力版で結果が追加されます。

J-Quants Free planはAPI制限が5件/分のため、複数銘柄の連続取得では余裕を持って15秒以上の間隔を空けます。日付を指定する場合は、直近12週間を避け、かつFree planの取得範囲内になる過去2年程度の日付を指定します。
一括取得は最初に `--limit 3` 程度で確認してから増やします。

レート制限（429）、提供元障害（5xx）、通信障害は取得不能として確定せず、再試行待ちとして記録します。正常応答で価格が存在しない場合だけ、該当銘柄・日付を`no_data`として扱います。

## 短期分析

Streamlitの「短期分析」タブでは、取得済みの日次終値を使って以下を表示します。

- 5日、20日、25日、50日、75日移動平均
- EMA 12、EMA 26
- RSI 14
- MACD、シグナル、ヒストグラム
- ボリンジャーバンド
- 1日、5日、20日騰落率
- 簡易短期スコアと加点・減点要因

FRED由来の指数データは高値、安値、出来高を含まないため、ローソク足、出来高、ATRは今後のデータソース追加後に表示します。

## 変動候補と仮想投資評価

「変動候補」タブでは、米国指数と日経平均の相関、直近の米国指数変動、日本株・ETFの短期指標を使い、大きく動きそうな候補と根拠を表示します。過去の仮想投資結果は未較正の参考統計として表示できますが、少数標本のノイズを避けるため候補スコアへ加減算しません。

「仮想投資評価」タブでは実注文を行いません。実データ評価では、対象日の終値を含む情報で判断し、その次の東証営業日の調整済み始値で買います。その後の日次OHLCで利益確定、損切り、保有期限を判定し、窓開けは始値、同一日足で両条件へ到達した場合は損切りを優先します。始値が欠損している場合は取引を生成せず、下方向シグナルは空売り利益へ変換せず観察専用にします。買いのみ口座は資金をエントリー時に拘束し、同じ現金の重複利用を防ぎます。

実データ評価用口座の初期設定は、250万円、1銘柄30%上限、同時保有2銘柄、100株単位、手数料0.10%、税率0%です。前営業日出来高だけを使って参加率上限と部分約定を決め、前営業日売買代金5千万円未満を除外し、売買代金帯ごとの代理スプレッド／スリッページを適用します。入力に取引停止・値幅制限・特別気配フラグがあれば約定不能として扱いますが、現在の取得元がこれらの実データを常に提供するわけではありません。税率は設定可能ですが、実際の税務を再現するものではありません。空売り、実際の板・特別気配・値幅制限データは未完成であり、実投資判断には使用しません。

企業行動は`corporate-actions-conservative-v1`で扱います。株式分割・併合では数量と1株当たりの取得単価・損切り・利益確定水準を調整し、現金配当は権利落ち日に保有数量を固定して支払日以降の最初の価格セッションで入金します。調整済み価格へ企業行動を二重反映しないよう、明示イベントがある銘柄は保存済みの未調整OHLCを使います。未調整OHLC、企業行動の確認済み期間、公表時刻、通貨のいずれかが不足する場合は警告または評価保留とし、値を推測しません。合併・株式交換、端株の現金化、外貨配当は未対応イベントです。

現時点では本番用の企業行動データソースを採用・自動収集していません。そのため通常画面の短期・中期口座と過去評価には「企業行動を確認できる期間情報がありません」と表示されます。これは計算エラーではなく、企業行動を確認せずに結果を確定しないための品質警告です。企業行動と確認済み期間のDBテーブルは`0014_corporate_actions`で追加されます。未確定から確定・取消への変更や確認範囲の更新は取得時刻ごとの改訂として追記し、分析時点で利用可能だった最新版だけを使います。

銘柄ライフサイクルは`asset-lifecycle-conservative-v1`で扱います。`0015_asset_lifecycle`は上場日・上場廃止日・時点別市場／業種・投資可能状態と、日付ごとの銘柄集合が完全かどうかを別テーブルへ改訂履歴として保存します。完全と確認できた過去スナップショットだけで当時の銘柄集合を復元し、現在残っている銘柄だけへ絞りません。保有中の銘柄が完全な次回スナップショットから消えた場合、または上場廃止が明示された場合は、推測した売却価格を使わず回収額0の保守評価で決済します。確認済み銘柄集合がない期間は通常口座で警告し、正式な実バックテストでは新規エントリーを拒否します。

J-Quants銘柄マスター収集は、レスポンスまたは`--date YYYYMMDD`で有効日を確認できた場合だけライフサイクルを保存します。`--limit`なしの全件応答だけを完全な銘柄集合とし、件数制限付き取得は部分集合として扱います。上場日・上場廃止日は提供されたフィールドだけを保存し、欠損値や「今回見当たらない」という事実から日付を推測しません。現在のMac DBには過去全日の完全スナップショットがまだ蓄積されていないため、通常画面の警告は安全装置として残ります。確認例は `docker compose exec app python jobs/collect_jquants_listed_info.py --date YYYYMMDD` です。

多通貨会計は`fx-accounting-jpy-usd-v1`で扱います。JPY口座でUSD資産を売買する場合、資産価格と損益はUSD、口座残高と最終損益はJPYで記録し、銘柄価格寄与、USD/JPY寄与、為替スプレッド／換算コスト、手数料・税を分離します。エントリー、決済、日次評価、外貨配当の各時点で、その時点までに利用可能な同日USD/JPYレートを要求します。レート欠損時は過去値を前方補完せず、エントリーまたは決済を見送り、保有評価額・未実現損益は「為替評価保留」とします。`0016_nullable_fx_valuation`は評価不能値を0と偽装せずNULLで台帳へ保存します。

現時点の通常口座は日本株・日本ETFが中心で、実運用可能な米国個別株・ETFの価格収集元と取引時刻対応FXデータは未採用です。FREDの`DEXJPUS`は市場分析用の日次参考値であり、取得時刻が取引時点より後なら約定レートとして使用しません。したがって多通貨計算基盤の完成は、米国資産による実投資支援の完成を意味しません。

画面の「現在結果を前向き観察として保存」ボタンは、将来結果を使う過去評価を研究JSONとして手動保存する旧経路です。`jobs/run_forward_shadow.py`の通常モードはこれと分離され、短期・中期口座を`delayed_historical`としてPostgreSQLへ追記保存し、`data/forward_shadow/<account>/delayed_historical/YYYY-MM-DD.json`へDB由来の監査コピーを出力します。このディレクトリはGit対象外です。同じ口座・系統・JST営業日の同一入力による再実行は冪等で、異なる後発入力による置換は拒否します。

通常モードの画面では、判断時刻以前の`price_time`と`available_at`だけを使う「指定時点の判断」を、将来結果を使った過去評価から分けて確認できます。買い候補だけでなく、待機とデータ不足も表示します。ただしJ-Quants Freeの遅延価格による研究結果であり、現在の買い判断ではありません。

通常モードには、短期`forward-short-term-v1`と中期`forward-mid-term-v1`の独立口座状態があります。各250万円から開始し、短期は利益確定8%・損切り5%・最大10営業日、中期は利益確定18%・損切り10%・最大60営業日です。現金、保有、予定注文、実現／未実現／累積損益、最大ドローダウンを次の呼び出しへ渡せます。画面では「仮想投資評価」タブの「短期・中期の独立仮想口座」で確認できます。

`NOW-P0-3`では、PostgreSQLへ短期・中期の口座、日次状態、判断、約定予定、約定、決済、見送り、日次残高を追記保存する基盤を追加しました。`NOW-P0-4`では、同じ口座・JST営業日でも`delayed_historical`と`current_market`を別々に固定できるようにし、観察日時、価格最終日、東証営業日基準の遅延日数、取得元、入力ハッシュ、品質状態、理由コードを保存します。同一入力・同一結果の再実行だけを冪等に受け付け、異なる後発入力による置換と台帳行のUPDATE／DELETEを拒否します。再起動後は系統別に最新状態を復元します。

Mac側DBは`0014_corporate_actions`まで適用済みです。新規環境では通常どおり次を実行します。

```bash
docker compose exec app alembic upgrade head
```

Mac定期ジョブは18:30 JSTを同日再試行共通の判断時刻として、`delayed_historical`だけをDBへ記録します。J-Quants Freeの遅延データは画面でも「研究上の買い候補」と表示し、`current_market`へ渡した場合は鮮度ゲートがシグナルを消去して`current_market_freshness_failed`を保存します。これは正式な6〜12か月の現在判断検証期間には算入しません。Raspberry Piには`0011`・`0012`とも未適用です。将来結果を使う旧1口座経路も研究用として残し、自動売買、証券口座への接続、実注文は行いません。

Macで平日18:30、20:30、22:30に上記の研究スナップショットを再試行する`launchd`設定は`docker/macos/com.arts1996.market-signal-lab-forward-shadow.plist`です。ログイン時にも呼びますが、JST 18:30より前、東証非営業日、当日保存済みの場合は分析前に正常終了します。2026-08-16にユーザー承認のもと`~/Library/LaunchAgents/com.arts1996.market-signal-lab-forward-shadow.plist`へ登録済みです。Macが停止中またはDocker Desktopが未起動の場合は後続時刻で再試行しますが、翌日に前日分を後付け生成しません。正式な継続口座完成後にラズパイを常時運用の実行主体へ切り替える手順は[docs/21_forward_shadow_operations.md](docs/21_forward_shadow_operations.md)を参照してください。

`NOW-P0-5`では、MacのLaunchAgentを`docker/macos/run-forward-shadow.zsh`経由へ変更しました。各試行の開始、成功、見送り、失敗は同じ試行IDを付けて`job_runs`へ追記します。Dockerへ到達できない場合だけはDBへ書けないため、Git対象外の`logs/forward-shadow-host-attempts.tsv`へ`docker_unavailable`として残します。Dockerは動くがDBへ到達できない場合は`database_unavailable`、保存先が512 MiB未満または使用率95%以上なら`output_capacity_insufficient`、DB正本とJSONが異なる場合は`json_modified`として区別します。DB状態がありJSONだけ欠ける場合はDBから再出力しますが、改変済みJSONは自動上書きしません。

利用者は画面上部の「システム管理」を選び、「前向き仮想口座の日次監視」で対象営業日、短期・中期の記録数、最終成功、当日失敗回数、欠測営業日、保存容量、JSON監査を確認できます。3回すべて失敗した日は警告し、翌日のデータで後付けしません。2026-08-16の非営業日試運転では`started`／`skipped`とホスト側成功を確認済みです。最初の実営業日に短期・中期の成功記録とJSONが作られることは継続監視項目です。

通常の`jobs/run_backtest.py`は、全J-Quants株・ETFのうち有効な調整済みOHLCVが東証営業日で連続する銘柄だけを使います。短期は学習60＋検証20の80営業日、中期は学習120＋検証20の140営業日を最低条件とし、各250万円、日経平均ベンチマーク、手数料0.10%、税率0%、前営業日売買代金と流動性コストを使って別系列で評価します。現状のMac DBは最大11営業日のため、両方とも`insufficient_contiguous_price_history`です。結果には入力ハッシュ、ルールハッシュ、費用、リスク規則、検証窓を保存します。TOPIX・単純保有との追加比較は`NOW-P2-3`の残課題です。

実データでは既定の`data/validation/live-windows.json`、デモでは`--validation-registry-path /app/data/validation/windows.json`で、シミュレーターを呼ぶ前に口座別の未見期間を登録します。同じ口座の重複期間を変更後ルールで再評価しようとすると停止します。短期口座と中期口座は別の評価系列です。レジストリはGit対象外であり、画面表示だけでは書き込みません。同じファイルを複数プロセスから同時更新する運用は行いません。

候補抽出、短期スクリーニング、仮想評価には、日本株・ETFについて東証カレンダー上で連続する、重複しない有効な調整済み日次データが30営業日以上必要です。年単位の空白や未取得セッションをまたいで騰落率を計算しません。20日指標はこの品質ゲート通過後に利用し、50日・75日移動平均はそれぞれ必要な観測数がそろった銘柄だけで利用します。

短期スクリーニングの「注目度」は、20日騰落率、ボラティリティ、RSI、ボリンジャーバンド、MACDの偏りを決定論的に整理した比較値です。上昇確率、買い推奨、将来予測ではありません。通常画面ではJ-Quants Free planの約12週間遅延を明示します。

## テスト

```bash
docker compose run --rm app pytest
```

ローカルPythonで実行する場合は、Python 3.12環境で以下を使います。

```bash
pip install -e ".[dev]"
pytest
```

## GitHub運用

作業は小さな単位でIssue化し、1つのIssueにつき1つの目的に絞ります。無料で使えるGitHub標準機能だけを前提にし、有料機能や判断がつかない外部サービスは導入前に確認します。

Issueの基本分類:

- `feature`: 新機能
- `bug`: 不具合
- `data`: データ取得・保存・品質
- `analysis`: 分析ロジック
- `ops`: Docker、Raspberry Pi、バックアップ、運用
- `docs`: READMEや手順書

推奨フロー:

```bash
git status
git pull
# 実装、テスト
git add <changed-files>
git commit -m "<短い変更内容>"
git push
```

変更前後の最低確認:

```bash
pytest
```

## Raspberry Piへの配置

前提:

- Raspberry Pi OS 64-bit
- Docker
- Docker Compose
- USB接続SSD推奨

手順:

```bash
git clone <your-repository-url>
cd market-signal-lab
cp .env.example .env
docker compose up -d --build
```

再起動後の自動復旧は `docker-compose.yml` の `restart: unless-stopped` で行います。Streamlitを直接インターネットへ公開せず、外部アクセスが必要な場合はTailscaleなどの利用を想定してください。

## セキュリティ

- APIキー、Slackトークン、DBパスワードは `.env` で管理し、Git、ログ、テスト結果、バックアップへ含めません。
- `.env.example` には実際の秘密値を記載しません。
- StreamlitとPostgreSQLを無制限にインターネット公開しません。PostgreSQLは内部ネットワークまたはlocalhostに限定します。
- 銘柄コード、期間、数値パラメータ、Slackコマンド、LLMのツール引数を境界で検証します。
- 任意SQL、任意コード、プロンプトによる指示上書きから外部操作を実行させません。
- 本番Raspberry Piへのデプロイ、DB変更、リストア、cron変更は、影響を確認してから実施します。
- 本番マイグレーションはMac側で検証し、事前バックアップを取得します。

## 定期実行

初期版ではcronまたはsystemd timerでジョブを呼び出します。例は [docker/cron.example](/Users/tsurusumu/Projects/market-signal-lab/docker/cron.example) にあります。

Raspberry Pi側のcronでは、以下のようにコンテナ内コマンドを呼び出す形にできます。

```cron
30 6 * * 1-5 cd /path/to/market-signal-lab && docker compose exec -T app python jobs/collect_us_market.py
40 6 * * 1-5 cd /path/to/market-signal-lab && docker compose exec -T app python jobs/run_short_term_analysis.py
0 23 * * * cd /path/to/market-signal-lab && docker compose exec -T app python jobs/backup_database.py
```

このリポジトリのラズパイ用設定は [docker/raspberry-pi.crontab](/Users/tsurusumu/Projects/market-signal-lab/docker/raspberry-pi.crontab) です。平日06:10（JST）にFREDの市場データを取得します。J-QuantsはDocker Composeの常駐 `jquants-collector` サービスが、1銘柄ずつ15秒以上の間隔で継続取得します。収集順は、取得可能な最新取引日を全銘柄で埋め、次に直近30取引日の欠損を新しい日付から補完し、その後に残りの約2年分を古い日付から補完します。

J-Quants銘柄マスターが空の場合、最初の実行で上場銘柄を件数制限なしで取得します。その後、最新の取得可能日（安全のため91日前）の全銘柄を優先して埋め、直近30取引日の欠損を新しい日付から補完してから、Free planの残り約2年分を古い日付から補完します。銘柄マスターは7日ごとに自動更新し、更新結果をジョブ履歴へ記録します。最新候補日が一時的に取得不能な場合は、同じ日を15秒ごとに再試行せず、標準6時間間隔で再確認し、その間は直近欠損または過去日の補完を継続します。進捗はDBに保存するため、ラズパイやコンテナが再起動しても、保存済みの銘柄・日付を避けて続行します。

15秒間隔を守るため、実効速度は最大4銘柄/分です。4,448銘柄の最新取得可能日を埋める初回処理は約18時間半が目安です。

ラズパイへの登録・確認:

```bash
mkdir -p ops_logs
crontab docker/raspberry-pi.crontab
crontab -l
docker compose exec app python jobs/collect_us_market.py
docker compose logs -f jquants-collector
docker compose exec app python jobs/collect_jquants_all_prices.py --limit 5 --lag-days 91 --recent-session-count 30 --history-days 720
```

J-Quants APIキー未設定、またはFree planで取得可能な日次データがない場合、ジョブはデータを作らず、取得ログとジョブ履歴にスキップ理由を残します。

## バックアップとリストア

バックアップ:

```bash
docker compose exec app python jobs/backup_database.py
```

`BACKUP_DIR` に `pg_dump --format=custom` のファイルを保存します。保存期間は `BACKUP_RETENTION_DAYS` で設定します。

バックアップジョブは、アプリ内のSQLAlchemy接続URLを`pg_dump`互換のPostgreSQL URLへ変換して実行します。

リストア例:

```bash
docker compose stop app
docker compose exec db dropdb -U market market_signal_lab
docker compose exec db createdb -U market market_signal_lab
docker compose run --rm -e PGPASSWORD="$POSTGRES_PASSWORD" app \
  pg_restore -h db -U market -d market_signal_lab /backups/<backup-file>.dump
docker compose up -d app
```

バックアップ作成側と同じPostgreSQLクライアントを使うため、復元も`app`コンテナの`pg_restore`を使用します。復元後はテーブル件数とAlembicの状態を確認してください。

## データと分析上の注意

- 時刻はDBにUTCで保存し、画面では日本時間に変換します。
- 欠損日は単純な前方補完で埋めません。
- 米国市場と日本市場の比較では、同日終値ではなく米国前営業日と日本当日を対応させます。
- 相関は因果関係を示しません。
- 統計的傾向は将来の値動きや利益を保証しません。
- APIキーやDBパスワードは `.env` で管理し、Gitには登録しません。
