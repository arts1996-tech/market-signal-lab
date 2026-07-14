# Market Signal Lab

日本株、米国株、日本ETF、米国ETFを対象に、市場環境、銘柄選定、エントリー、利益確定、損切り、ポジションリスクの判断材料を整理する投資判断支援システムです。短期は1〜20営業日、中期は1か月〜1年程度を想定します。

日経平均、TOPIX、米国主要指数、VIX、USD/JPY、金利等は市場環境や円建て評価の参考データとして扱います。FX取引そのものは投資対象にしません。

このシステムは自動売買や証券会社への発注を行いません。「必ず上がる」「買うべき」といった断定や利益保証をせず、統計的な傾向、根拠、反対材料、過去検証、データ品質を提示します。最終的な投資判断と注文は利用者が行います。

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
18. `docs/17_remediation_todo.md`

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
- 米国指数と日本指数の相関、個別銘柄の短期指標、仮想投資評価のフィードバックを使った変動候補タブ
- 実注文を行わない仮想投資評価タブ。候補に出した理由、損益、結果理由を表示
- 指数比較チャート、相関グラフ、取得ログ、ジョブ履歴
- pytestによる主要ロジックのテスト
- 画面上部の通常／デモ、データ時点、取得元、価格基準、対象期間、品質警告の共通表示
- 銘柄・ETFの技術指標スクリーニング基盤（少数履歴は除外、財務値は推測しない）

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

SEC財務データを明示的に1銘柄取得する場合は、User-Agentを設定したうえで次を実行します。対象銘柄が既存の`assets`にない場合、データは保存せず取得件数だけを表示します。

```bash
docker compose run --rm app python jobs/collect_sec_fundamentals.py --cik 0000320193 --symbol AAPL
```

CIKの資産マスター登録は、SEC公式の銘柄一覧JSONとUSD資産を明示指定して実行します。日本株・日本ETF、既存CIKとの重複はジョブが拒否します。

```bash
docker compose run --rm app python jobs/map_sec_cik.py \
  --json /path/to/company_tickers_exchange.json \
  --symbol AAPL
```

3. 初回起動またはマイグレーション変更時は、バックアップ確認後にDBマイグレーションを明示実行します。

```bash
docker compose run --rm app alembic upgrade head
```

4. Docker Composeで起動します。

```bash
docker compose up --build
```

5. ブラウザで開きます。

```text
http://localhost:8501
```

起動時にAlembicマイグレーションが実行されます。通常モードではサンプルデータを投入せず、実データだけを分析対象にします。価格がない場合はFREDまたはJ-Quantsの収集ジョブを実行してください。

### デモ用サンプルデータ

合成データは実データと混ぜません。デモとして使う場合だけ、`MARKET_DATA_MODE=demo` を設定してから明示的に投入します。

```bash
MARKET_DATA_MODE=demo docker compose run --rm app python jobs/seed_sample_data.py --demo
MARKET_DATA_MODE=demo docker compose up --build
```

デモモードの画面は合成データだけを表示し、投資判断には使用できません。既存DB内のサンプル行も通常モードの分析・画面からは除外されます。

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
docker compose exec app python jobs/run_backtest.py
```

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

「変動候補」タブでは、米国指数と日経平均の相関、直近の米国指数変動、日本株・ETFの短期指標、過去の仮想投資フィードバックを使い、大きく動きそうな候補と根拠を表示します。

「仮想投資評価」タブでは実注文を行いません。過去時点で候補に出たと仮定し、一定営業日後の損益、候補にした理由、結果の理由を確認します。仮想投資の結果は銘柄別に集計され、次回以降の候補抽出スコアにフィードバックされます。

候補抽出と仮想評価には、日本株・ETFの日次データが30営業日以上必要です。

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

このリポジトリのラズパイ用設定は [docker/raspberry-pi.crontab](/Users/tsurusumu/Projects/market-signal-lab/docker/raspberry-pi.crontab) です。平日06:10（JST）にFREDの市場データを取得します。J-QuantsはDocker Composeの常駐 `jquants-collector` サービスが、1銘柄ずつ15秒以上の間隔で継続取得します。

J-Quants銘柄マスターが空の場合、最初の実行で上場銘柄を件数制限なしで取得します。その後、最新の取得可能日（安全のため91日前）の全銘柄を優先して埋め、完了後は未取得日を古い順に、Free planの約2年の範囲まで補完します。銘柄マスターは7日ごとに自動更新します。進捗はDBに保存するため、ラズパイやコンテナが再起動しても、保存済みの銘柄・日付を避けて続行します。

15秒間隔を守るため、実効速度は最大4銘柄/分です。4,448銘柄の最新取得可能日を埋める初回処理は約18時間半が目安です。

ラズパイへの登録・確認:

```bash
mkdir -p ops_logs
crontab docker/raspberry-pi.crontab
crontab -l
docker compose exec app python jobs/collect_us_market.py
docker compose logs -f jquants-collector
docker compose exec app python jobs/collect_jquants_all_prices.py --limit 5 --lag-days 91 --history-days 720
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
