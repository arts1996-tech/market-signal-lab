# Market Signal Lab Agent Guide

このファイルは、Codexなどの開発エージェントが毎回最初に読む前提のプロジェクトガイドです。

## アプリ名

Market Signal Lab

## 目的

日本株、国内ETF、米国主要指数、為替を対象に、短期取引と一部中期投資の判断材料を提示するWebアプリです。自動売買は実装しません。表示は強気、中立、慎重などのシナリオ形式にし、投資助言ではないことを明記します。

## 主要技術

- Python 3.12
- Streamlit
- PostgreSQL
- SQLAlchemy
- Alembic
- pandas / numpy
- Plotly
- statsmodels
- httpx / tenacity
- pydantic-settings
- pytest
- Docker / Docker Compose

## プロジェクト構造

```text
.
├── app/
│   ├── analysis/       # 日次リターン、相関、回帰、スコア、バックテスト
│   ├── collectors/     # FREDなど外部APIクライアント
│   ├── core/           # 設定、ログ、例外
│   ├── dashboard/      # Streamlit画面
│   ├── database/       # SQLAlchemyモデル、Repository、Alembic
│   └── services/       # 収集・分析サービス
├── docker/             # cron例など運用補助
├── jobs/               # cron/systemd timerから呼べる独立コマンド
├── tests/              # pytest
├── alembic.ini
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── README.md
└── AGENTS.md
```

## 役割分担

開発時は以下の3つの観点を必ず持つこと。

- 作業者 (Developer): 要求を満たす実装、リファクタリング、バグ修正を行う。
- 評価者 (Evaluator): 設計、可読性、セキュリティ、運用面の問題をレビューする。
- テスター (Tester): テストを作成・実行し、正常系と異常系を確認する。

ユーザーへの報告では、必要に応じて `[作業者]` `[評価者]` `[テスター]` の短い観点を示す。ただし、内部の長い推論は出さず、実施内容と判断結果を簡潔に伝える。

## 開発ルール

- 欠損日は単純な前方補完で埋めない。
- DB保存時刻はUTC、画面表示は日本時間。
- 米国市場と日本市場の比較は、同日終値ではなく米国前営業日と日本当日を対応させる。
- 相関は因果関係ではないことを画面に明記する。
- APIキー、DBパスワードは `.env` で管理し、Gitに含めない。
- 外部APIクライアント、DB処理、分析ロジック、UIは分離する。
- 同一価格データは `asset_id + timeframe + price_time + source` で重複登録を防ぐ。
- Raspberry Pi運用を想定し、x86専用依存を避ける。

## よく使うコマンド

```bash
cp .env.example .env
docker compose up --build
docker compose exec app python jobs/collect_us_market.py
docker compose exec app python jobs/seed_sample_data.py
docker compose run --rm app pytest
```

ローカル検証でDockerが使えない場合:

```bash
python3.12 -m venv .venv312
.venv312/bin/python -m pip install -e ".[dev]"
.venv312/bin/python -m pytest
```

## 初期版の完了条件

- Docker ComposeでPostgreSQLとStreamlitが起動する。
- Alembicで初期スキーマが作成される。
- サンプルデータまたはFREDデータがDBへ保存される。
- StreamlitでNASDAQ、Dow Jones、S&P 500、日経平均、USD/JPYの比較チャートを確認できる。
- NASDAQ Composite前営業日と日経平均当日の相関分析を確認できる。
- pytestが通る。

