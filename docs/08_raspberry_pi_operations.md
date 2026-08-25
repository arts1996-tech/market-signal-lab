# 08 Mac・Raspberry Pi運用

## 1. 役割

- Mac: 通常開発、pytest、Compose起動、マイグレーション往復、UI確認、隔離データ検証。
- Raspberry Pi: 承認済みコードのデータ収集、PostgreSQL、定期処理、将来の正式な前向き運用。
- Macで確認できない開発フローにしない。Raspberry Piは節目の統合・運用確認先とする。
- Raspberry Piの状態変更は、影響とロールバックを提示し、利用者の明示承認後だけ行う。

## 2. 接続情報

- ホスト: `raspberrypi.local`
- IP: DHCP等で変わるため固定値を前提にしない。名前解決できない場合だけ現在IPを確認する。
- SSHユーザー: `tsurusumu`
- Mac側鍵: `~/.ssh/travel_price_monitor_rpi`
- 接続例: `ssh -i ~/.ssh/travel_price_monitor_rpi tsurusumu@raspberrypi.local`
- ED25519指紋: `SHA256:TZq+z1REo3s9bBcUcVGvhfDb5Hj6pqrH/vtuo37Ws6I`
- 配置先: `/home/tsurusumu/market-signal-lab`

指紋が一致しない場合は接続せず利用者へ確認する。秘密鍵、パスフレーズ、`.env`の値はリポジトリへ保存しない。

## 3. 現行のデータ収集

- PostgreSQLはlocalhostまたは内部ネットワークだけへ公開する。
- `jquants-collector`は`restart: unless-stopped`の常駐サービスとし、cronで二重実行しない。
- 収集順は、取得可能な最新取引日の全銘柄、直近30取引日の欠損を新しい日付から、残り履歴を古い日付からの順とする。
- 進捗を`price_collection_targets`と`price_collection_items`へ保存し、再起動後は未取得分から再開する。
- J-Quants Free運用では安全側の15秒以上の間隔を維持し、429・5xxは再試行、正常応答のデータなしは銘柄単位の`no_data`として区別する。
- 1銘柄の失敗で日付全体を取得不能にしない。既存価格、進捗、`no_data`、`retry_pending`を破棄しない。
- MacとRaspberry Piで同じAPIキーのcollectorを同時起動しない。

## 4. Macの暫定前向きジョブ

ユーザーLaunchAgent `com.arts1996.market-signal-lab-forward-shadow`は、平日18:30、20:30、22:30とログイン時に`jobs/run_forward_shadow.py --daily --not-before-jst 18:30`を呼ぶ。

- 18:30より前、東証非営業日、当日保存済みの場合は保存せず正常終了する。
- 同日の再試行は共通の18:30判断時刻を使い、最初に成功した結果だけを不変保存する。
- Macがスリープ中なら復帰時、Docker停止等なら後続時刻で再試行する。
- 翌日に前日の入力を再構成せず、失敗日は欠測として残す。
- Mac DBのJ-Quants Freeデータによる`delayed_historical`研究であり、`current_market`や正式な6〜12か月の前向き成績へ数えない。
- 短期・中期のDB台帳を正本とし、JSONは`data/forward_shadow/<account>/delayed_historical/YYYY-MM-DD.json`へ監査出力する。
- Dockerへ到達できない試行は`logs/forward-shadow-host-attempts.tsv`へ、DB到達後の状態は`job_runs`へ記録する。

## 5. 監査と欠測

- 候補なし、品質ゲート未達、見送りも結果として保存する。
- 同日を異なる後発入力で置換しない。
- JSONは連番、ファイルハッシュ、前レコードハッシュ、自身のハッシュ、末尾headを持つ。
- `jobs/verify_audit_integrity.py`でDB正本との一致、欠落、改変、順序、末尾削除、未登録を検査する。
- 異常検出時は自動追記・再出力を止め、証拠を保全して手動復旧する。
- Docker停止、DB停止、容量不足、欠測を別の運用障害として集計する。
- 同一ホスト内の監査チェーンは、管理権限を持つ攻撃者による全ファイル再生成までは証明しない。

## 6. Raspberry Piへの正式切替

正式な前向き運用はMacとRaspberry Piの一方だけを実行主体にする。推奨構成はDockerジョブを呼ぶoneshot systemd serviceと、平日18:30、20:30、22:30の`Persistent=true` timerである。翌日へ持ち越した実行でも前日分を後付け生成しない。

切替手順:

1. 対象品質ゲートを完了し、Macの実営業日で短期・中期の日次記録を確認する。
2. Raspberry PiのDBバックアップを取得する。
3. service／timer、マイグレーション、容量見積りをMacまたは隔離環境で確認する。
4. コードと設定を限定配置し、手動で1回試運転する。
5. DB、JSON、ログ、終了コード、データ鮮度を確認する。
6. Mac LaunchAgentを停止・無効化し、単一実行主体であることを確認する。
7. Raspberry Pi再起動後にCompose、collector、timerが手動操作なしで復旧することを確認する。

Raspberry Pi停止中の正式記録をMacの古いDBで代替しない。

## 7. デプロイとマイグレーション

- Macで全関連テストを成功させてからGit経由で配置する。
- `.env`、秘密、ログ、DB、バックアップをGitへ含めない。
- 本番マイグレーション前にrevision、バックアップ、downgrade可否、停止影響を確認する。
- Compose起動時にマイグレーションを自動適用しない。破壊的変更は別移行として承認を得る。
- 配置後は対象テスト、HTTPヘルス、DB revision、collector継続、ログを確認する。

## 8. バックアップ・復旧

- `pg_dump`を日次実行し、成功・失敗、サイズ、保存先、保持期間、削除を記録する。
- バックアップとDBを同じ障害領域だけに置かない。
- 定期的に一時DBへリストアし、主要テーブルとマイグレーション状態を検証する。
- `restart: unless-stopped`、ヘルスチェック、構造化ログ、ログローテーションを維持する。
- CPU、メモリ、ディスク、DB容量、温度、ジョブ時間を実測し、重い分析が収集を圧迫しないようにする。
- USB SSDを使う場合は接続、給電、マウント、再起動、切断時の復旧を実機確認する。

### 非破壊のリストア検証

本番DBを削除せず、一時DBへ復元して確認する。次は既定のDBユーザー`market`を使う例であり、設定を変更している場合は`.env`のユーザー名へ置き換える。

```bash
docker compose exec db dropdb --if-exists -U market market_signal_lab_restore_check
docker compose exec db createdb -U market market_signal_lab_restore_check
docker compose exec db pg_restore -U market -d market_signal_lab_restore_check /backups/<backup-file>.dump
docker compose exec db psql -U market -d market_signal_lab_restore_check -c "SELECT version_num FROM alembic_version;"
docker compose exec db psql -U market -d market_signal_lab_restore_check -c "SELECT COUNT(*) FROM assets;"
docker compose exec db dropdb -U market market_signal_lab_restore_check
```

復元中に失敗した場合は一時DBを調査用に残し、原因確認前に本番DBへ適用しない。本番DBへの復元は停止時間、対象バックアップ、現行DBの追加バックアップ、ロールバックを提示し、利用者の明示承認後にだけ行う。

## 9. ネットワークと障害対応

- StreamlitとPostgreSQLを無制限にインターネット公開しない。
- 外部アクセスが必要な場合はTailscale等の料金、規約、認証、失効、復旧を確認し、承認後に導入する。
- ログの秘密マスキングをテストする。
- 将来のSlack通知は補助であり、Slack障害時もDB記録とローカル運用確認を維持する。

本運用は仮想評価と研究のためのものであり、実注文や予測能力の証明には使わない。
