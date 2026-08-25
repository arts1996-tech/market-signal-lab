# Market Signal Lab Agent Guide

## 1. 優先順位と文書地図

本書はこのリポジトリで作業するCodex・開発エージェント向けの最上位指示です。実装前にGit状態、既存コード、設定、マイグレーション、テストを確認してください。

要件の優先順位は次のとおりです。

1. 本書の安全境界、開発原則、必須手順
2. [docs/README.md](docs/README.md)が示す各分野の製品・安全・機能・受け入れ正本
3. [docs/22_current_priority_todo.md](docs/22_current_priority_todo.md)の進捗、実装順、完了状態。機能正本の詳細要件を上書きしない
4. [README.md](README.md)の現行起動・確認手順
5. 既存コードとテストが示す現在の挙動

- 文書、コード、運用状態が矛盾する場合は勝手に削除・単純化せず、差分、影響、推奨案を先に報告する。
- レビュー・提案は、要件変更と明記されない限り必須要件と区別する。
- 実装済み機能をロードマップ順序だけを理由に削除・無効化しない。
- 進捗と次タスクは`docs/22_current_priority_todo.md`だけで更新する。AGENTS、README、機能仕様へ日付別実装日誌や古い次タスクを追加しない。
- UIは製品要件に合わせて全面再設計できる。ただしデータ、分析、DB、ジョブ、秘密管理の要件は維持する。
- 仮想口座、スコア、バックテスト、実投資への利用可否を扱う前に[docs/19_investment_decision_readiness_assessment.md](docs/19_investment_decision_readiness_assessment.md)を読む。品質ゲート完了まで実売買や投資額決定の主な根拠にしない。

## 2. 製品境界

Market Signal Labは日本株、米国株、日本ETF、米国ETFの投資判断材料を整理する研究・判断支援システムです。短期は1〜20営業日、中期は1か月〜1年程度を想定します。

- 取引モードは現物、信用買い、信用売り、自動選択。現行実装と将来要件を画面・記録で区別する。
- 短期・中期の仮想口座は各250万円で分離し、資金移動を行わない。
- 利用者指定ティッカー、テーマETF、ナレッジ更新の詳細は各機能正本へ委譲する。
- FXは円建て評価と市場環境の参考であり、FX取引は対象外。

### 禁止事項

- 自動売買、証券会社への発注・予約、口座認証、ブラウザ自動操作を実装しない。
- 利益保証や断定的な売買推奨を表示しない。最終判断と注文は利用者が行う。
- LLMに未検証の価格・指標計算をさせない。存在しないOHLCV、ニュース、費用、信用可否を生成・補完しない。
- 投資信託、暗号資産、FX取引、先物・オプションは初期対象外とする。範囲変更は製品仕様の更新と利用者の明示承認後に行う。
- 利用者入力やLLMから任意SQL、任意コード、許可外の外部操作を実行しない。
- 仮想成績やナレッジ候補を、独立した未見検証と利用者承認なしに稼働ルールへ自動反映しない。

## 3. アーキテクチャと開発原則

- Python 3.12、Streamlit、PostgreSQL、SQLAlchemy 2.x／Alembic、pandas／numpy、Plotly、pytest、Docker Composeを基本とする。
- Apple Silicon MacとRaspberry PiのDebian系ARM64で動かし、x86専用依存を導入しない。
- 通常開発と確認はMacで行い、ラズパイはフェーズ完了、マイグレーション、ジョブ、バックアップ等の節目だけで確認する。
- 各変更単位でアプリを起動可能に保ち、一度に対象外フェーズまで実装しない。
- `app/collectors/`は外部API、`app/providers/`は交換可能な境界、`app/database/`は永続化、`app/analysis/`と`app/backtest/`は決定論的計算、`app/services/`はユースケース、各dashboardは表示に限定する。
- ダッシュボードへAPI、DB、分析、約定ロジックを埋め込まない。
- Pythonで計算・検証した必要最小限の構造化データだけを将来のLLMへ渡す。Gemini停止時もPythonとStreamlitで縮退運転できるようにする。
- API障害を局所化し、タイムアウト、再試行、レート制限、冪等性、失敗分類を実装する。
- 既存の未コミット変更は利用者のものとして保全し、無関係な変更、reset、checkout、破壊的な一括書換えを行わない。

## 4. データ・分析の必須ルール

- DB保存時刻はUTC、画面はJSTを基本とし、取引所タイムゾーン、セッション日、`effective_at`、`available_at`、`fetched_at`を区別する。
- 同一価格は少なくとも`asset_id + timeframe + price_time + source`で重複を防ぐ。
- 取得元、取得時刻、鮮度、欠損、改訂、品質、入力ハッシュ、ルール版を保存・表示する。
- 欠損を安易に前方補完せず、取得元にない始値・高値・安値・出来高・ATR等を推測しない。
- サンプル、デモ、実データを暗黙混在させない。未登録sourceへ暗黙フォールバックしない。
- `delayed_historical`と`current_market`をDB、画面、口座、監査で分離する。J-Quants Freeの約12週遅延価格、古いMac DB、合成データを現在判断へ昇格させない。
- 古い価格と最新ニュースを混在させない。分析時点で利用可能だった情報だけを使う。
- 日米市場を同一日付で単純結合せず、取引セッション、休日、連休、夏時間、次に開く日本営業日を扱う。
- 将来データ、先読み、サバイバーシップ、多重比較、過学習、検証期間の再利用を防ぐ。
- 相関は因果ではなく、Granger検定は予測上の先行性に限定する。標本数、期間、信頼区間、不確実性を表示する。
- 現物、信用買い、信用売りは別系列で評価する。信用可否、在庫、費用、保証金等が不足する場合は推測せず抑制・禁止する。
- 現行の前向きジョブは遅延研究用であり、正式な6〜12か月の`current_market`観察期間として数えない。

## 5. 外部サービスと料金

- 原則無料の技術・APIを優先する。有料サービス、無料条件が不明なもの、将来的な課金リスクがあるものは導入前に利用者へ確認する。
- 採用前に公式情報で料金、無料枠、レート制限、履歴、遅延、保存・表示・再配布条件、私的利用条件を再確認する。
- J-Quants上位プラン、新しい価格source、ニュース、Gemini、Claude、OpenAI、Slack等は、利用可能になっただけで実装・有効化を承認されたとは扱わない。
- [docs/28_external_capability_activation_readiness.md](docs/28_external_capability_activation_readiness.md)に従い、能力単位の公式確認、費用承認、Mac隔離検証、品質ゲート、前向きシャドー、`current_market`承認、Raspberry Pi配置承認を順に満たす。
- Free由来データを上書きせずsource・時点・版を分離する。後日のバックフィルを正式な前向き観察へ遡及算入しない。
- スクレイピングは規約、安定性、保守性を確認し、安易に採用しない。

## 6. セキュリティと運用変更

- APIキー、トークン、DBパスワードは`.env`等のGit対象外領域で管理し、ログ、例外、テスト、バックアップへ出さない。`.env.example`の秘密値は空にする。
- StreamlitとPostgreSQLを無制限に公開しない。PostgreSQLは内部ネットワークまたはlocalhostに限定する。
- 外部アクセスはTailscale等の料金・規約・構成を確認し、承認後に導入する。
- SQLAlchemyの安全なクエリを使い、銘柄、期間、数値、外部API応答を境界で検証する。
- Raspberry Piへの配置、DB変更、復元、cron／timer変更等は、対象・影響・バックアップ・ロールバックを示して利用者の明示依頼または承認後だけ行う。
- 本番マイグレーションはMacで往復検証し、事前バックアップを取得する。破壊的変更を自動適用しない。
- 正式な前向きジョブはMacとRaspberry Piで二重実行しない。実行主体を切り替えるときは[docs/08_raspberry_pi_operations.md](docs/08_raspberry_pi_operations.md)に従う。

## 7. 実装前後の必須手順

### 実装前

1. `pwd`、`git status`、対象ディレクトリと既存ファイルを確認する。
2. 本書、`docs/22_current_priority_todo.md`、対象機能の正本、READMEを読む。
3. 対象コード、設定、マイグレーション、テスト、ジョブを確認する。
4. 仕様との差分、矛盾、不足、外部条件、セキュリティ・運用リスクを整理する。
5. 変更範囲、完了条件、テスト方法を決める。

### 実装後

1. 対象テストと必要な回帰テストを実行する。通常は`docker compose run --rm app pytest`を使う。
2. 起動、設定、DB、運用が変わる場合だけREADMEまたは該当正本を更新する。
3. `git diff --check`を行い、秘密、`.env`、ログ、バックアップ、DBデータがGit対象でないことを確認する。
4. 変更、DB影響、確認方法、テスト結果、制約、次タスク、残タスクを報告する。
5. 区切りのよい変更は、テスト成功後に目的単位でコミットする。

テスト詳細は[docs/12_acceptance_criteria.md](docs/12_acceptance_criteria.md)を正本とする。Dockerが使えない場合はPython 3.12仮想環境で`python -m pytest`を実行する。

## 8. Raspberry Pi接続

接続・収集・バックアップ・切替の正本は[docs/08_raspberry_pi_operations.md](docs/08_raspberry_pi_operations.md)です。

- 通常接続先: `raspberrypi.local`。IPはDHCPで変わり得るため固定値を前提にしない。
- SSHユーザー: `tsurusumu`
- Mac側鍵: `~/.ssh/travel_price_monitor_rpi`
- ED25519指紋: `SHA256:TZq+z1REo3s9bBcUcVGvhfDb5Hj6pqrH/vtuo37Ws6I`
- 配置先: `/home/tsurusumu/market-signal-lab`
- 指紋が変わった場合は接続せず、利用者へ確認する。秘密鍵本体やパスフレーズはプロジェクトへ保存しない。

## 9. GitHubと報告

- リポジトリ: `https://github.com/arts1996-tech/market-signal-lab`
- 1コミット／Issueは一つの目的に絞り、PRには目的、変更、テスト、DB影響、制約、料金方針への影響を記載する。
- 完了報告は必要に応じて`[作業者]`、`[評価者]`、`[テスター]`の観点を短く示す。
- 一区切りごとに、完了内容だけでなく次の1タスクと残タスクを提示する。

現行の次タスクと残タスクは常に[docs/22_current_priority_todo.md](docs/22_current_priority_todo.md)を参照する。
