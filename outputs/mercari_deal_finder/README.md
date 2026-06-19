# メルカリ割安検出MVP

指定キーワードでメルカリの公開検索ページを確認し、売り切れ相場の中央値より安い出品をCSVに保存するMVPです。条件に合う候補があればSlack通知もできます。

## 注意

- ログイン、購入、自動いいね、自動コメント、自動購入は行いません。
- メルカリの仕様変更やアクセス制限で取得できない場合があります。
- 過剰アクセスしないよう、キーワードごとにランダム待機を入れています。
- CAPTCHA、ログイン要求、ブロック画面が出た場合は無理に突破しません。
- ブランド品は偽物リスクがあるため、警告ワードや付属品ワードを表示します。最終判断は人が行います。
- 2026-06-19に `Apple Watch` の実検索で、CSV保存とノイズ除外を確認済みです。

## フォルダ構成

```text
outputs/mercari_deal_finder/
├── mercari_deal_finder.py
├── config.json
├── requirements.txt
├── run_mercari_deal_finder.sh
├── data/
│   ├── mercari_deals_YYYYMMDD.csv
│   └── mercari_deals_YYYYMMDD.md
└── logs/
    └── app.log
```

## インストール

```bash
cd outputs/mercari_deal_finder
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
```

成功すると、最後にエラーが出ずプロンプトへ戻ります。

## 設定

`config.json` で変更できます。

- `keywords`: 検索キーワード
- `min_discount_rate`: 割安率。0.2なら20%以上安い商品
- `min_score`: Slack通知する最低スコア
- `slack_webhook_url`: Slack Incoming Webhook URL。空の場合は環境変数 `SLACK_WEBHOOK_URL` を使います
- `max_current_items_per_keyword`: 現在出品中の商品取得件数
- `max_sold_items_per_keyword`: 売り切れ商品の相場取得件数
- `keyword_include_words`: キーワードごとの必須寄せワード。例: `iPad Pro` は `iPad Pro` を含む商品名に寄せる
- `keyword_exclude_words`: キーワードごとの除外語。例: Apple Watchのバンド、ケース、充電ケーブルなど

`Apple Watch` はアクセサリーが大量に混ざるため、初期設定でバンド、ケース、フィルム、充電、ケーブル、ループ、空箱などを除外しています。
`Tiffany` と `ヴァンクリーフ` は偽物リスクが高いため、高リスクブランドとして警告を出します。

Webhookをファイルに書きたくない場合:

```bash
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/xxxxx"
```

## まず動作確認

メルカリへアクセスせず、サンプルデータでCSV保存だけ確認します。

```bash
cd outputs/mercari_deal_finder
source .venv/bin/activate
python mercari_deal_finder.py --sample --dry-run
```

成功すると:

```text
CSV saved: data/mercari_deals_YYYYMMDD.csv
Markdown saved: data/mercari_deals_YYYYMMDD.md
```

のように表示されます。

## ライブ取得

```bash
python mercari_deal_finder.py --dry-run
```

Slackへ通知しないでCSVだけ保存します。

Slack通知まで行う場合:

```bash
python mercari_deal_finder.py
```

## Slack通知テスト

```bash
python mercari_deal_finder.py --test-slack
```

Slackに `メルカリ割安検出テスト` が届けば成功です。

## 1キーワードだけ試す

```bash
python mercari_deal_finder.py --keyword "Apple Watch" --dry-run
```

## CSV出力項目

- 検索キーワード
- 商品名
- 現在価格
- 相場中央値
- 割安率
- 状態
- 送料
- 出品日時
- いいね数
- 商品URL
- 警告ワード
- 仕入れ候補スコア

Markdown出力には、商品名、価格、割安率、警告、商品リンクをまとめて表示します。

## cron設定例

毎日9:10、13:10、21:10に実行する例です。

```cron
10 9 * * * /bin/bash /home/ubuntu/f_tools/mercari_deal_finder/run_mercari_deal_finder.sh >> /home/ubuntu/f_tools/mercari_deal_finder/logs/cron.log 2>&1
10 13 * * * /bin/bash /home/ubuntu/f_tools/mercari_deal_finder/run_mercari_deal_finder.sh >> /home/ubuntu/f_tools/mercari_deal_finder/logs/cron.log 2>&1
10 21 * * * /bin/bash /home/ubuntu/f_tools/mercari_deal_finder/run_mercari_deal_finder.sh >> /home/ubuntu/f_tools/mercari_deal_finder/logs/cron.log 2>&1
```

VPSへ置く場合:

```bash
mkdir -p /home/ubuntu/f_tools/mercari_deal_finder
cp -a outputs/mercari_deal_finder/. /home/ubuntu/f_tools/mercari_deal_finder/
```
