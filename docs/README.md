# 仕様書ガイド

仕様書は役割別に読みます。すべてを毎回読む必要はありません。現行の実装順と残タスクは`22_current_priority_todo.md`だけを正本とします。

## 常時参照

| 文書 | 役割 | 参照タイミング |
| --- | --- | --- |
| [01 製品ビジョン](01_product_vision.md) | 対象資産、期間、非目標 | 製品範囲を変えるとき |
| [09 セキュリティ・品質](09_security_and_quality.md) | 秘密、入力、投資分析品質 | すべての変更 |
| [10 開発ロードマップ](10_development_roadmap.md) | 安定したフェーズ依存 | フェーズを確認するとき |
| [12 受け入れ基準](12_acceptance_criteria.md) | テスト可能な完了条件 | 実装・レビュー時 |
| [19 投資判断支援としての評価](19_investment_decision_readiness_assessment.md) | 現在の利用可能範囲と品質ゲート | スコア・口座・実投資を扱うとき |
| [22 現行ToDo](22_current_priority_todo.md) | 次タスク、残タスク、依存順 | 作業開始・完了時 |

## アーキテクチャ・データ・分析

| 文書 | 正本とする内容 |
| --- | --- |
| [02 システムアーキテクチャ](02_system_architecture.md) | 実行構成、モジュール境界、非機能要件 |
| [03 投資分析](03_investment_analysis.md) | 共通指標、判断カード、分析横断ルール |
| [04 日米波及](04_us_japan_spillover.md) | 米国前営業日と日本次営業日の対応・統計 |
| [07 DB設計](07_database_design.md) | 論理データモデル、時点、来歴、追記台帳 |
| [データソース方針](data_sources.md) | provider境界、採用状態、公式条件記録 |
| [20 仮想口座判断ロジック](20_virtual_account_decision_logic.md) | 現行シグナル、数量、決済、判断系統 |
| [28 外部能力有効化](28_external_capability_activation_readiness.md) | 制約解除後の開始・昇格・停止条件 |

## 機能別仕様

| 文書 | 対象 |
| --- | --- |
| [05 Gemini](05_gemini_agents.md) | LLM境界、構造化出力、監査、任意の複数LLM検証 |
| [06 Slack](06_slack_integration.md) | Socket Mode、認可、通知、永続化境界 |
| [23 信用取引](23_margin_trading.md) | 現物・信用買い・信用売り・自動選択 |
| [24 指定ティッカー](24_user_selected_ticker_simulation.md) | 利用者指定集合の分析・仮想口座 |
| [25 ナレッジ更新](25_simulation_knowledge_feedback.md) | 仮想結果の振り返りとルール昇格 |
| [26 Market Signal Lite](26_market_signal_lite.md) | 日常確認用の別UI |
| [27 テーマ・セクターETF](27_theme_sector_etf.md) | テーマ定義、スコア、流動性、ETF候補 |

## 運用

| 文書 | 対象 |
| --- | --- |
| [08 Raspberry Pi運用](08_raspberry_pi_operations.md) | Mac/Raspberry Pi、収集、切替、バックアップ、復旧 |

## 更新ルール

- 安定した製品・設計要件は各仕様へ記載する。
- 未完了タスク、次の作業、完了状態は`22_current_priority_todo.md`だけへ記載する。
- 日付別作業日誌、過去のDB件数、古いpytest件数、バックアップ名はGit履歴または運用ログへ残し、仕様書へ蓄積しない。
- 外部サービスの公式確認結果は`data_sources.md`、制約解除後の昇格状態は`28_external_capability_activation_readiness.md`へ記載する。
- 削除済みのレビュー・履歴文書が必要な場合はGit履歴を参照し、repo内にarchiveを再作成しない。
