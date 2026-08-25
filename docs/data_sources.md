# Data Source Policy

Market Signal Labは原則無料で利用できるデータソースを優先します。有料サービス、有料API、有料プラン、無料条件が不明なもの、将来的な課金リスクが判断できないものは、導入前に必ずユーザー確認を行います。

現在の制約が上位プランや新providerで解消できる場合の、必要情報、状態、開始条件、Mac検証、`current_market`昇格、Raspberry Pi配置は`docs/28_external_capability_activation_readiness.md`を正本とする。契約済みであることと、本番判断へ利用できることを同一視しない。

## 現在の採用方針

| 用途 | データソース | 採用方針 | 理由 |
| --- | --- | --- | --- |
| 米国指数、日経平均、USD/JPY | FRED API | 採用 | APIキー取得後に無料で利用でき、すでにDB保存まで確認済み |
| 日本株、国内ETFの履歴四本値 | J-Quants API Free plan | 採用候補 | 月額0円。公開プラン表では過去2年、12週間遅延の株価四本値が対象 |
| 日本株、国内ETFの財務情報 | J-Quants API | 単銘柄入口のみ、Freeでの自動収集は保留 | 2026-08-16の公式公開プラン表ではFreeの財務情報は対象外表示。契約中プランで利用可能か確認が必要 |
| 日本株、国内ETFの直近データ | J-Quants API Free plan | 短期判断の最新データ用途には不採用 | Free planは直近12週間を除く2年分で、当日株価は取得できない |
| TOPIX四本値、指数四本値 | J-Quants有料プラン | 保留 | Free planには含まれないため、利用前にユーザー確認が必要 |
| 分足、ティック、前場四本値 | J-Quants有料またはアドオン | 保留 | 有料条件があるため、利用前にユーザー確認が必要 |
| 信用残、貸借・空売り関連 | J-Quants有料プラン等 | 未採用 | プラン、履歴、更新時刻、私的利用条件を再確認し、ユーザー承認が必要 |
| 一般信用在庫、信用金利、貸株料、保証金条件 | 証券会社または承認済みprovider | 未採用 | 証券会社固有で変動し、認証・規約・自動取得可否の確認が必要 |
| 米国short availability、borrow fee、margin requirement | 未定 | 未採用 | 無料・保存・表示条件を満たす本番ソースが未確定 |
| 東証ETFの名称、コード、対象指数、売買単位、上場日 | JPX ETF銘柄一覧・銘柄別資料 | 公式参照として採用 | テーマ候補の識別・照合に使用。価格、流動性、信用在庫を保証しない |
| 制度信用・貸借選定 | JPX 制度信用・貸借選定銘柄一覧 | 公式参照候補 | 公表時点の市場制度区分の確認用。一般信用在庫、証券会社別条件、将来の取引可否を示さない |
| テーマETFのBid/Ask、板厚、NAV、純資産、構成銘柄、資金フロー | 未定 | 未採用 | 短期テーマランキングと流動性評価に必要。無料・時点・保存・表示条件を確認して承認が必要 |
| テーマ別の商品、金利、政策・地政学ニュース | 未定 | 未採用 | テーマごとに必要な現在値・過去時点・利用可能時刻・保存権を満たすproviderが未確定 |

## J-Quants Free Planで扱う範囲

無料方針に従い、初期実装ではJ-Quants Free planの公式公開範囲だけを対象にします。

- 上場銘柄一覧
- 株価四本値
- 取引カレンダー

制約:

- 株価データは12週間遅延
- 取得可能な過去データは直近12週間を除く2年分
- 公式FAQはレートリミットの閾値を非公表としている。既存運用は安全余裕のあるローカル設定として15秒以上の間隔を維持するが、公式上限とは表現しない
- CSVダウンロードはFree plan対象外
- 個人の私的利用に限定し、取得データの第三者配信やデータを利用したアプリ提供を行わない
- Freeは登録後1年で自動解約と案内されているため、期限と契約状態を運用時に確認する

## 実装方針

1. J-Quants APIキーは `.env` で管理する。
2. APIクライアントは `app/collectors/jquants.py` に分離する。
3. 公開閾値が非公表のレート制限へ安全側で対応するため、既存ジョブ側で15秒以上の待機、キャッシュ、差分取得を行い、429時は再試行する。
4. 日本株・ETFの短期分析では、無料版データが遅延していることを画面に明記する。
5. 最新性が必要な分析には、FREDなど既存データ、手入力、またはユーザー確認済みの別データソースだけを使う。
6. 有料プランが必要な機能は実装前に止めて確認する。
7. プラン変更後も能力ごとに提供範囲を確認し、Free由来データを上書きせず、source・`available_at`・版を分離する。
8. 後から取得した過去データを正式な前向き観察へ遡及算入しない。
9. 信用取引データは`effective_at`、`available_at`、`fetched_at`、対象市場・証券会社を保存し、現在値を過去バックテストへ適用しない。
10. 信用取引の本番ソースが未採用の間は型・provider境界・合成データテストに限定し、信用可否や費用を推測しない。
11. JPXのETF・信用銘柄一覧は識別と公表区分の照合に用い、実際の流動性、証券会社別在庫、費用、現在の取引可否へ暗黙転用しない。
12. テーマデータは`docs/27_theme_sector_etf.md`に従い、説明変数ごとに`effective_at`、`available_at`、`fetched_at`、source、権利、品質を保持する。
13. テーマランキングは、現在価格・ニュース等が未採用の間は`delayed_historical`研究に限定し、最新ニュースを遅延価格へ混ぜない。

## Provider境界と来歴

外部APIを分析ロジックや画面へ直接結合しない。providerは必要な範囲で次の共通能力を実装する。

- `fetch_assets`
- `fetch_prices`
- `fetch_fundamentals`
- `fetch_events`
- `fetch_etf_profile`
- `fetch_theme_factors`
- `fetch_liquidity_snapshots`
- `health_check`

取得済み範囲をDBへ保存し、差分取得、キャッシュ、同一リクエスト抑制、レート制限記録を行う。各値にはsource、source symbol、`effective_at`、`available_at`、`fetched_at`、revision、qualityを保存する。取得不能値をゼロや取引可能として補完しない。

## 取引カレンダー

- JPX公式Market HolidaysとNYSE公式Holidays & Trading Hoursを照合の正本とする。
- 実行時は`exchange_calendars`の`XTKS`と`XNYS`を使用できるが、公式との差異、臨時休場、早期終了、将来年の範囲を検証する。
- カレンダーライブラリは価格sourceや来歴を置き換えない。差異がある期間は分析から除外する。

## 財務・ETF・米国データの現在地

- J-Quants財務、SEC Company Facts、レビュー済みETF JSONは共通収集サービス、入力検証、資産照合、冪等保存、取得ログ、`JobRun`、失敗分類へ接続済み。
- J-Quants財務とSECは単銘柄を明示した実行だけを維持し、自動全銘柄収集は公式条件と利用者承認まで有効化しない。
- SEC Company FactsはUSD資産とCIKの完全一致、識別User-Agent、Fair Accessを必須とし、価格sourceとして使わない。
- ETF経費率、NAV、構成銘柄等はJ-Quants銘柄マスターから推測せず、取得元と利用条件を確認したJSONだけを`jobs/save_etf_metrics.py`で明示投入する。
- 無料で継続更新され、広範囲の日次収集と画面表示条件を満たす米国個別株・ETF価格providerは未採用。Alpha Vantage、Twelve Data、Nasdaq Data Link等も、現在の料金・上限・表示権を公式再確認し、利用者承認前に採用しない。

## 2026-08-16 公式条件の再確認

- [J-Quants公式サイト](https://jpx-jquants.com/?lang=ja%2F): Freeは月額0円、登録後1年で自動解約、価格は過去2年・12週間遅延。公開プラン表でFreeの財務情報は対象外表示。利用は個人の私的利用に限定され、第三者配信・データ利用アプリの提供は禁止。レートリミット閾値は非公表。
- [SEC EDGAR Data APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces): Company Facts等はAPIキーなしで公開され、XBRLデータを取得できる。大量取得には公式bulk ZIPが推奨される。
- [SEC Webmaster FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions): 自動アクセスは組織名と連絡先を含むUser-Agentを宣言し、最大10リクエスト/秒を超えない。

この確認結果により、J-Quants財務とSEC Company Factsは単銘柄を明示したジョブだけを維持する。自動全銘柄収集は、対象プラン、私的利用範囲、レート制限、実行頻度を提示し、ユーザー承認を得るまで実装・有効化しない。SECで将来大量取得が必要な場合は、1社ずつの連打ではなく公式bulk ZIPも先に比較する。

## 2026-08-26 テーマETF候補の公式照合

- [JPX ETF全銘柄](https://www.jpx.co.jp/equities/products/etfs/issues/01.html)、[日本株テーマ別ETF](https://www.jpx.co.jp/equities/products/etfs/issues/01-04.html)、[外国株ETF](https://www.jpx.co.jp/equities/products/etfs/issues/01-08.html)で、`docs/27_theme_sector_etf.md`の初期候補コードと名称を照合した。
- [JPX 制度信用・貸借選定銘柄一覧](https://www.jpx.co.jp/listing/others/margin/index.html)は毎月更新されるため、取得時点を保存し、一般信用や証券会社別在庫へ推測適用しない。
- 552A、568A、577A、578A、579A、580A、610A等の新規ETFは履歴が短い。公式掲載だけで短期売買に十分な流動性があるとは判定せず、出来高、売買代金、スプレッド、板、NAV、純資産、上場日数を別途評価する。

## 実装済みの無料株価データ基盤

J-Quants Free planの株価データについて、以下は実装済みです。

1. `.env.example` に `JQUANTS_API_KEY=` を追加
2. J-Quants APIクライアントの骨格作成
3. 上場銘柄一覧の取得と `assets` への保存
4. 国内ETFと日本株のFree plan範囲の四本値保存
5. 画面上に「J-Quants Free planデータは12週間遅延」と表示

## J-Quants Free Planの実行例

APIキーを `.env` の `JQUANTS_API_KEY` に設定したあと、1銘柄ずつ取得します。

```bash
docker compose exec app python jobs/collect_jquants_listed_info.py --limit 20
docker compose exec app python jobs/collect_jquants_daily.py --code 86970 --date 20260401 --name "JPX" --asset-type stock
docker compose exec app python jobs/collect_jquants_daily_batch.py --date 20260401 --limit 3
```

複数銘柄を取得する場合は、閾値非公表のレート制限に対して余裕を持ち、既存運用どおり15秒以上の間隔を空けます。日付は直近12週間を避け、かつFree planの取得範囲内になる過去2年程度の日付を指定します。一括取得は最初に `--limit 3` 程度で確認してから増やします。
