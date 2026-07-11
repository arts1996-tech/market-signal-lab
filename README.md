# Market Signal Lab

日本株、国内ETF、米国主要指数、為替の短期・中期判断材料を整理するWebアプリです。初期版では、PostgreSQLに保存した日次市場データを使い、NASDAQ Composite、Dow Jones、S&P 500、日経平均、USD/JPYの比較チャートと相関分析をStreamlitで表示します。

このアプリは自動売買を行いません。「必ず上がる」「買うべき」といった断定も行わず、統計的な傾向と根拠を表示するための土台です。

GitHub: https://github.com/arts1996-tech/market-signal-lab

## 開発方針

本システムは原則として無料で利用できる技術、サービス、API、ライブラリで開発します。有料サービス、有料API、有料プランが必要な機能、無料枠の条件が不明なもの、将来的な課金リスクを判断しきれないものは、導入前に必ず確認します。

データソースの採用方針は [docs/data_sources.md](/Users/tsurusumu/Projects/market-signal-lab/docs/data_sources.md) にまとめます。

## 初回実装範囲

- Python 3.12 / Streamlit / PostgreSQL / SQLAlchemy / Alembic
- Docker ComposeによるMacBookとRaspberry Pi OS 64-bit向け起動構成
- 資産マスターと日次価格テーブル
- FRED APIクライアント
- NASDAQ Composite、Dow Jones、S&P 500、日経平均、USD/JPYの保存
- APIキー未設定時でも動くサンプルデータ投入
- 日次リターン、米国前営業日と日本当日の対応
- 20日、60日、120日、250日相関
- 60日ローリング相関
- 米国株指数と日本株指数の相関結果をDBへ蓄積し、後続分析で再利用できる構成
- 短期分析タブで移動平均、EMA、RSI、MACD、ボリンジャーバンド、簡易短期スコアを表示
- 指数比較チャート、相関グラフ、取得ログ、ジョブ履歴
- pytestによる主要ロジックのテスト

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

## MacBookでの起動

1. 環境ファイルを用意します。

```bash
cp .env.example .env
```

2. FRED APIキーを使う場合は `.env` の `FRED_API_KEY` に設定します。未設定でもサンプルデータで画面確認できます。

J-Quants Free planを使う場合は `.env` の `JQUANTS_API_KEY` に設定します。J-Quants Free planの株価は12週間遅延です。

3. Docker Composeで起動します。

```bash
docker compose up --build
```

4. ブラウザで開きます。

```text
http://localhost:8501
```

起動時にAlembicマイグレーションとサンプルデータ投入が自動実行されます。

## 市場データ取得

FRED APIキーを設定したあと、以下を実行するとDBに実データを保存します。取得済みデータは一意制約で重複登録を防ぎます。

```bash
docker compose exec app python jobs/collect_us_market.py
```

相関分析結果をDBへ蓄積する場合は、以下を実行します。米国前営業日リターンと日本当日リターンの対応で、ペア別相関、米国指数群と日本指数群の平均リターン同士の相関、ペア別相関の平均サマリーを `correlation_results` に保存します。

```bash
docker compose exec app python jobs/run_short_term_analysis.py
```

個別の入口は以下です。

```bash
docker compose exec app python jobs/collect_japan_market.py
docker compose exec app python jobs/collect_fx.py
docker compose exec app python jobs/collect_jquants_daily.py --code 86970 --name "JPX" --asset-type stock
docker compose exec app python jobs/run_mid_term_analysis.py
docker compose exec app python jobs/run_backtest.py
```

J-Quants Free planはAPI制限が5件/分のため、複数銘柄の連続取得では余裕を持って15秒以上の間隔を空けます。

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

## 定期実行

初期版ではcronまたはsystemd timerでジョブを呼び出します。例は [docker/cron.example](/Users/tsurusumu/Projects/market-signal-lab/docker/cron.example) にあります。

Raspberry Pi側のcronでは、以下のようにコンテナ内コマンドを呼び出す形にできます。

```cron
30 6 * * 1-5 cd /path/to/market-signal-lab && docker compose exec -T app python jobs/collect_us_market.py
40 6 * * 1-5 cd /path/to/market-signal-lab && docker compose exec -T app python jobs/run_short_term_analysis.py
0 23 * * * cd /path/to/market-signal-lab && docker compose exec -T app python jobs/backup_database.py
```

## バックアップとリストア

バックアップ:

```bash
docker compose exec app python jobs/backup_database.py
```

`BACKUP_DIR` に `pg_dump --format=custom` のファイルを保存します。保存期間は `BACKUP_RETENTION_DAYS` で設定します。

リストア例:

```bash
docker compose stop app
docker compose exec db dropdb -U market market_signal_lab
docker compose exec db createdb -U market market_signal_lab
docker compose exec db pg_restore -U market -d market_signal_lab /backups/<backup-file>.dump
docker compose up -d app
```

## データと分析上の注意

- 時刻はDBにUTCで保存し、画面では日本時間に変換します。
- 欠損日は単純な前方補完で埋めません。
- 米国市場と日本市場の比較では、同日終値ではなく米国前営業日と日本当日を対応させます。
- 相関は因果関係を示しません。
- 統計的傾向は将来の値動きや利益を保証しません。
- APIキーやDBパスワードは `.env` で管理し、Gitには登録しません。
