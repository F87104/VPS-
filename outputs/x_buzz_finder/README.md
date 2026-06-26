# Xバズ返信候補検索ツール

Xで伸びている投稿を探し、返信候補としてCSV・Markdown・Slackに出すMVPです。

このツールは投稿、返信、いいね、フォローを行いません。候補を探すだけです。

## 目的

- 伸びている投稿を早めに見つける
- 返信しやすい投稿をカテゴリ別に並べる
- 短い返信案をコピー用コードブロックで出す
- Slackへ候補リンクを送る

## ファイル

```text
outputs/x_buzz_finder/
├── x_buzz_finder.py
├── config.json
├── run_x_buzz_finder_vps.sh
├── data/
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
- `reply_templates`: SlackとMarkdownに出す短い返信案

## 見方

Markdownの上から順に見るのがおすすめです。

- スコアが高い: 伸びている可能性が高い
- 返信が多い: 議論が動いている
- リポストが多い: 拡散されている
- 表示が多い: 広く見られている

返信は自動化せず、本文を読んでから手動で行います。

Slackには上位候補のURLと短い返信案が出ます。返信案はコピーしやすいようにコードブロックで表示します。
