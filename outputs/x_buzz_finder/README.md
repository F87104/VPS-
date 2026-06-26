# Xバズ返信候補検索ツール

Xで伸びている投稿を探し、返信候補としてCSV・Markdown・Slackに出すMVPです。

このツールは投稿、返信、いいね、フォローを行いません。候補を探すだけです。

## 目的

- 伸びている投稿を早めに見つける
- 返信しやすい投稿をカテゴリ別に並べる
- 元ポストの数字・固有名詞を拾った短い返信案を出す
- クスッと笑える大喜利風の返信案も出す
- スマホ用の返信ダッシュボードを生成できる
- 外部公開を有効化した場合、Slackへ10件の候補一覧とダッシュボードURLを送る

## ファイル

```text
outputs/x_buzz_finder/
├── x_buzz_finder.py
├── config.json
├── run_x_buzz_finder_vps.sh
├── run_dashboard_server_vps.sh
├── data/
├── public/
└── logs/
```

## ローカルでサンプル確認

```bash
python3 outputs/x_buzz_finder/x_buzz_finder.py --sample --dry-run
```

成功すると:

```text
CSV saved: ...
Markdown saved: ...
Candidates: 2
```

OpenAIを使わず、ルール版だけで確認したい場合:

```bash
python3 outputs/x_buzz_finder/x_buzz_finder.py --sample --dry-run --rule-replies
```

## VPSで実行

VPSでは既存のXログイン状態を使います。

```bash
/bin/bash /home/ubuntu/f_tools/x_buzz_finder/run_x_buzz_finder_vps.sh
```

## cron例

返信候補を朝・昼・夕のX稼働前に探す例です。

```cron
30 6 * * * /bin/bash /home/ubuntu/f_tools/x_buzz_finder/run_x_buzz_finder_vps.sh >> /home/ubuntu/f_tools/x_buzz_finder/logs/cron.log 2>&1
45 11 * * * /bin/bash /home/ubuntu/f_tools/x_buzz_finder/run_x_buzz_finder_vps.sh >> /home/ubuntu/f_tools/x_buzz_finder/logs/cron.log 2>&1
15 18 * * * /bin/bash /home/ubuntu/f_tools/x_buzz_finder/run_x_buzz_finder_vps.sh >> /home/ubuntu/f_tools/x_buzz_finder/logs/cron.log 2>&1
```

## 設定

`config.json` で変更できます。

- `queries`: 探すテーマ
- `min_likes`: 最低いいね数
- `min_reposts`: 最低リポスト数
- `min_replies`: 最低返信数
- `min_views`: 最低表示数
- `max_age_hours`: 古すぎる投稿を除外する時間
- `exclude_words`: 触らない方がいい投稿の除外語
- `reply_generation`: `auto`ならOpenAI APIキーがある時だけ返信案を磨く
- `reply_ai_max_posts`: OpenAIで返信案を作る上位候補数
- `slack_copy_message_top_posts`: コピー用の短い別メッセージを送る上位候補数
- `dashboard_top_posts`: 返信ダッシュボードに出す候補数
- `dashboard_enabled`: 返信ダッシュボードHTMLを作るかどうか
- `dashboard_base_url`: Slackに載せる返信ダッシュボードURL。外部公開を許可してから設定する
- `openai_model`: 空欄なら `.env` の `OPENAI_MODEL` を使う

## 見方

Markdownの上から順に見るのがおすすめです。

- スコアが高い: 伸びている可能性が高い
- 返信が多い: 議論が動いている
- リポストが多い: 拡散されている
- 表示が多い: 広く見られている

返信は自動化せず、本文を読んでから手動で行います。

外部公開を有効化した場合、Slackには10件の候補一覧と返信ダッシュボードURLを送ります。
返信ダッシュボードでは、各候補ごとに `通常コピー` `大喜利コピー` `元ポストを開く` を押せます。
Slack Incoming Webhookだけでは本物のクリップボードコピーボタンを作れないため、コピー操作はスマホ用HTMLダッシュボード側で行います。

返信案は2段構えです。

1. まず元ポスト内の数字・固有名詞・テーマ語を拾ったルール版を作る
2. `OPENAI_API_KEY` がある場合だけ、上位候補をOpenAIで自然なF文体と大喜利風へ磨く
