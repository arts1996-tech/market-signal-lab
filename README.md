# Market Signal Lab

日本株、米国株、日本ETF、米国ETFを対象に、市場環境、銘柄選定、売買計画、ポジションリスク、仮想口座の結果を整理する投資判断支援システムです。短期は1〜20営業日、中期は1か月〜1年程度を想定します。

自動売買や証券会社への発注は行いません。統計的な傾向、根拠、反対材料、データ品質を表示し、最終判断と手動注文は利用者が行います。

> 現在の仮想口座と候補スコアは、実際の売買や投資額決定の主な根拠にできる成熟度ではありません。利用可能範囲と必須品質ゲートは[投資判断支援としての正直な評価](docs/19_investment_decision_readiness_assessment.md)を確認してください。

> J-Quants Freeの日次価格は約12週間遅延するため、現在の短期判断には使いません。遅延研究`delayed_historical`と現在判断`current_market`は分離します。

GitHub: https://github.com/arts1996-tech/market-signal-lab

## アプリ

| アプリ | URL | 用途 |
| --- | --- | --- |
| Market Signal Lab | http://localhost:8501 | 研究、分析、データ・運用確認 |
| Market Signal Lite | http://localhost:8502 | 保存済み結果の日常確認 |

LiteはLabと同じDB・サービス層を読み、分析や約定ロジックを複製しません。どちらも証券会社への注文機能を持ちません。

## 文書

- 開発エージェントの最上位ルール: [AGENTS.md](AGENTS.md)
- 仕様書の役割別索引: [docs/README.md](docs/README.md)
- 現行の次タスク・残タスク: [docs/22_current_priority_todo.md](docs/22_current_priority_todo.md)
- 投資判断への利用可能範囲: [docs/19_investment_decision_readiness_assessment.md](docs/19_investment_decision_readiness_assessment.md)
- Raspberry Pi・バックアップ・実行主体切替: [docs/08_raspberry_pi_operations.md](docs/08_raspberry_pi_operations.md)

実装時は全仕様を毎回読むのではなく、`docs/README.md`から対象機能の正本を選びます。

## 技術構成

- Python 3.12
- Streamlit
- PostgreSQL
- SQLAlchemy 2.x / Alembic
- pandas / numpy / Plotly / statsmodels
- pytest
- Docker / Docker Compose
- Apple Silicon Mac / Raspberry Pi ARM64

主なディレクトリ:

```text
app/collectors/       外部APIクライアント
app/providers/        交換可能なデータ取得境界
app/database/         ORM・Repository・マイグレーション
app/analysis/         指標・相関・スコア等の決定論的計算
app/backtest/         約定・仮想口座・検証・監査
app/services/         ユースケース
app/dashboard/        Lab画面
app/lite_dashboard/   Lite画面
jobs/                 独立実行ジョブ
tests/                pytest
docs/                 仕様・運用手順
docker/               運用テンプレート
```

## Macで起動

### 1. 環境変数

初回だけ`.env.example`を`.env`へコピーし、必要な値を設定します。APIキーやパスワードをGitへ追加しないでください。

```bash
cp .env.example .env
```

主な任意設定:

- `FRED_API_KEY`: FRED収集
- `JQUANTS_API_KEY`: J-Quants収集
- `SEC_USER_AGENT`: SEC Company Facts
- `MARKET_DATA_MODE=demo`: 明示的なデモモード

### 2. 起動

新規環境では、DBを先に起動してマイグレーションを適用します。

```bash
docker compose up -d db
docker compose run --rm app alembic upgrade head
docker compose exec app python jobs/seed_theme_definitions.py
docker compose up -d --build app lite
```

既存環境でマイグレーション変更がない通常起動は次だけです。Raspberry PiのJ-Quants常駐収集と競合させないため、Mac側collectorを起動しません。

```bash
docker compose up -d --build db app lite
```

マイグレーション変更時は、バックアップと対象revisionを確認してからDB起動後に`alembic upgrade head`を実行します。Compose起動時には自動適用しません。

### 3. 状態確認

```bash
docker compose ps
docker compose logs --tail=100 app
docker compose logs --tail=100 lite
```

停止:

```bash
docker compose down
```

## デモデータ

通常データとサンプルを混在させないため、デモ投入は明示的に実行します。

```bash
MARKET_DATA_MODE=demo docker compose run --rm app python jobs/seed_sample_data.py --demo
MARKET_DATA_MODE=demo docker compose up -d --force-recreate app lite
```

デモ成績は戦略の有効性や期待収益の証明ではありません。

現物、信用買い、信用売り、事前条件による自動選択は、Mac上の合成・遅延研究用バックテスト基盤まで実装済みです。結果は取引モード別に分離し、追記専用DBへ保存できます。実信用データ、企業行動の完全な時点履歴、画面表示、現在判断には未接続であり、実売買の根拠や注文には使用しません。

通常モードへ戻す場合:

```bash
docker compose up -d --force-recreate app lite
```

## 主なジョブ

```bash
# 米国指数・為替等
docker compose exec app python jobs/collect_us_market.py

# J-Quants銘柄マスター／少数銘柄での確認
docker compose exec app python jobs/collect_jquants_listed_info.py --limit 20
docker compose exec app python jobs/collect_jquants_recent_daily_batch.py --limit 3 --lag-days 91 --lookback-days 5

# 分析・検証
docker compose exec app python jobs/run_short_term_analysis.py
docker compose exec app python jobs/run_spillover_analysis.py --jp-symbol 13060
docker compose exec app python jobs/run_mid_term_analysis.py
docker compose exec app python jobs/run_backtest.py

# 運用・監査
docker compose exec app python jobs/check_operations.py
docker compose exec app python jobs/verify_audit_integrity.py
docker compose exec app python jobs/backup_database.py
```

J-Quants Freeは遅延とレート制限を考慮し、少数件から確認します。Raspberry Piの常駐collectorが動いている間、同じAPIキーでMac collectorを同時実行しません。収集設計と採用状態は[データソース方針](docs/data_sources.md)を参照してください。

## テスト

通常:

```bash
docker compose run --rm app pytest
```

Dockerが使えない場合:

```bash
python3.12 -m venv .venv312
.venv312/bin/python -m pip install -e ".[dev]"
.venv312/bin/python -m pytest
```

変更対象に応じた受け入れ条件は[docs/12_acceptance_criteria.md](docs/12_acceptance_criteria.md)を確認します。

## データと安全上の注意

- FREDの終値系列へ存在しないOHLCVを生成しません。
- J-Quants Freeの遅延価格を現在判断へ使いません。
- 古い価格と最新ニュースを組み合わせません。
- 欠損値、信用可否、費用、板情報を推測しません。
- 相関を因果関係として扱いません。
- スコアは上昇確率、勝率、売買推奨ではありません。
- 正式評価は未見検証と前向き観察を必要とします。
- 外部有料サービスは、公式条件と費用を確認して利用者承認後に導入します。

## Raspberry Pi

接続情報、J-Quants収集順、Macからの実行主体切替、マイグレーション、バックアップ・リストア、障害時対応は[docs/08_raspberry_pi_operations.md](docs/08_raspberry_pi_operations.md)へ集約しています。

Raspberry Piの配置、DB、cron／timer、バックアップ状態を変更する操作は、Macでの検証と利用者の明示承認後に行います。
