# 02 システムアーキテクチャ

## 実行構成

MacBookで開発・検証し、承認済み機能をRaspberry Pi OS 64-bit上のDocker Composeで継続運用する。

現行の実装経路:

```text
External APIs
  -> Collectors
  -> PostgreSQL
  -> Python Analysis / Backtest
  -> Streamlit Lab / Lite
```

GeminiとSlackは将来の承認制フェーズであり、現行の必須経路ではない。導入後もPython分析とStreamlitだけで縮退運転できるようにする。

## サービス
- `db`: PostgreSQL
- `app` / `lite`: Streamlit
- `collector`: データ取得ジョブ
- `analyzer`: 指標・統計・スコア・バックテスト
- `gemini-agent`: 将来のGemini連携
- `slack-bot`: 将来のSlack Bolt / Socket Mode
- `scheduler`: cronまたはsystemd timer。初期は単純で透明性の高い方式を優先

## モジュール境界
- collectors: 外部API固有処理
- providers: 共通データ取得インターフェース
- database: ORM、repository、migration
- analysis: 決定論的な数値計算
- backtest: 時点整合的な検証
- agents: Geminiプロンプト、ツール、構造化出力
- slack_bot: コマンド、イベント、Block Kit
- dashboard: 表示のみ。計算ロジックを置かない
- jobs: 独立実行可能なジョブ
- core: 設定、ログ、例外、時刻

## ディレクトリ
```text
market-signal-lab/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── app/
│   ├── core/
│   ├── database/
│   ├── collectors/
│   ├── providers/
│   ├── analysis/
│   ├── backtest/
│   ├── dashboard/
│   └── lite_dashboard/
├── jobs/
├── tests/
├── docs/
```

## 非機能要件
- ARM64対応
- 冪等なデータ取得
- API障害の局所化
- 再試行、タイムアウト、レート制限
- DB永続化とバックアップ
- 再起動後の自動復旧
- 監査可能な分析履歴
