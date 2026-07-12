# 11 API・データソース方針

## 原則
- 無料または無料枠を優先
- 公式・信頼できるデータを優先
- 利用規約、再配布条件、レート制限を確認
- スクレイピングは規約と安定性を確認し、安易に採用しない
- プロバイダーを交換可能にする

## 候補
- J-Quants: 日本株・財務・取引日等（契約・無料枠の最新条件を実装時に確認）
- FRED: 金利・マクロ
- Alpha Vantage: 株価等
- Finnhub: 株価・企業イベント等
- SEC EDGAR: 米国企業開示
- その他、利用規約上問題のないデータ源

## 取引カレンダー（P0-3後続）

- JPXの公式「Market Holidays」を日本市場の休場日確認の正本とする。休場日は将来変更され得るため、実装・更新時に公式ページを再確認する。
- NYSEの公式「Holidays & Trading Hours」を米国市場の休場日・夏時間確認の正本とする。早期終了日は日次終値分析の休場とは区別して記録する。
- 実行時のセッション判定には、Apache-2.0ライセンスのPythonライブラリ`exchange_calendars`を用いる候補とする。MacとPython 3.12コンテナで東京`XTKS`とNYSE`XNYS`を確認済み。公式カレンダーとの差異、臨時休場、将来年の更新範囲は公式情報で検証し、差異がある期間は分析から除外する。
- このライブラリはカレンダー計算用であり、価格データの取得元・来歴を置き換えない。

## 実装要件
各プロバイダーは共通インターフェースを実装する。
- fetch_assets
- fetch_prices
- fetch_fundamentals
- fetch_events
- health_check

## キャッシュ
- DBに取得済み範囲を保存
- 差分取得
- 同一リクエストの抑制
- レート制限情報を記録

## データ来歴
- source
- source_symbol
- fetched_at
- available_at
- revision/version（取得可能な場合）
- quality_status
を保存する。
