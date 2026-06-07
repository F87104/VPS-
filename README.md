# VPS Morning Slack Tools

さくらVPS（Ubuntu 24.04）で、毎朝6:00にSlackへ相場チェックを自動送信するための運用メモとコード置き場です。

## 進捗

| 項目 | 状態 |
| --- | --- |
| VPS接続 | 完了 |
| Slack Incoming Webhook送信 | 完了 |
| OpenAI API連携 | 完了 |
| USDJPY / GOLD / NASDAQ / S&P500 / NYダウ取得 | 完了 |
| 専門ニュース取得（日経 / Bloomberg / Reuters / WSJ優先） | 完了 |
| 投資家F文体での朝メモ生成 | 完了 |
| cron毎朝6:00 JST実行 | 完了 |
| エラーログ出力 | 完了 |
| 再起動後のcron継続 | 完了 |
| Substackいいね・フォロー補助ツール安全化 | 完了 |
| Substack補助ツールのVPS毎日12:00自動起動 | 完了 |
| Substack終了ログのSlack通知 | 完了 |
| Xいいね・フォロー補助ツールの件数上限追加 | 完了 |
| X補助ツールのVPS配置 / Playwright導入 | 完了 |
| X終了ログのSlack通知 | 完了 |
| XのVPSログイン状態 | 完了 |
| Xのcron自動起動 | 7:00 / 12:30 / 19:00に設定済み |
| noteスキ・フォロー補助ツール作成 | 完了 |
| note補助ツールのVPS配置 | 完了 |
| note終了ログのSlack通知 | 完了 |
| noteのVPSログイン状態 | 完了 |
| noteのスキ実行確認 | 完了 |
| noteのフォロー実行確認 | 完了 |
| noteのcron自動起動 | 時刻指定待ち |
| GitHub push | リモートURL待ち |

## 現在のVPS構成

VPS上の本番パス:

```text
/home/ubuntu/f_tools/
├── .env
├── slack_test.py
├── morning_market_check.py
├── requirements.txt
├── trend_oracle_v2.8.0_pro_news_triple_choice.py
├── logs/
│   ├── morning_market_check.log
│   └── cron.log
└── slack_test.py.bak_*
```

本番実行ファイル:

```text
/home/ubuntu/f_tools/slack_test.py
```

cron設定:

```cron
0 6 * * * /usr/bin/python3 /home/ubuntu/f_tools/slack_test.py >> /home/ubuntu/f_tools/logs/cron.log 2>&1
```

## Slack配信内容

毎朝6:00 JSTに以下の構成で送信します。

```text
🌅 朝イチ相場CHECK
📊 主要指数
💹 ドル円
🪙 GOLD
📈 NASDAQ
📈 S&P500
📈 NYダウ

こんにちは、Fです✨

📌 Fの朝イチ相場CHECK
📰 今日のニュース3選
  Fアクション
  逆説メモ
💌 Fの投稿案（3つのバリエーション）

投資家Fより💌
```

## 投資家F文体ルール

現在の生成方針:

- 冒頭は `こんにちは、Fです✨`
- 一人称は `私`
- ニュース内の固有名詞、数字、仕組みを入れる
- 需給、資金の質、投資家心理へつなげる
- 実体験や直近の行動を想起させる一言を入れる
- 表面的な意味とは逆の可能性を入れる
- `Fアクション` には観察、確認、メモ、アラート設定、指値位置の見直しなどを入れる
- アクションの中に少し遊び心があり、クスッと笑える比喩を入れる
- 最後は `投資家Fより💌`

## ローカル成果物

このリポジトリに置いている主なファイル:

- [outputs/morning_market_check.py](outputs/morning_market_check.py): VPSで動かしている朝Slack通知コード
- [outputs/requirements.txt](outputs/requirements.txt): 必要Pythonライブラリ
- [outputs/SETUP_GUIDE.md](outputs/SETUP_GUIDE.md): 初心者向けVPSセットアップ手順
- [outputs/substack_like_follow_safe.py](outputs/substack_like_follow_safe.py): 安全寄りに直したSubstackいいね・フォロー補助ツール
- [outputs/run_substack_daily_vps.sh](outputs/run_substack_daily_vps.sh): VPSで毎日12:00に実行するためのラッパースクリプト
- [outputs/notify_slack_summary.py](outputs/notify_slack_summary.py): 終了ログをSlackへ通知するスクリプト
- [outputs/auto_like_v11_2_1_limited.py](outputs/auto_like_v11_2_1_limited.py): 件数上限つきにしたXいいね・フォロー補助ツール
- [outputs/run_x_daily_vps.sh](outputs/run_x_daily_vps.sh): VPSでX補助ツールを実行し、終了ログをSlackへ通知するラッパースクリプト
- [outputs/note_suki_follow_safe.py](outputs/note_suki_follow_safe.py): noteのスキ・フォロー補助ツール
- [outputs/run_note_daily_vps.sh](outputs/run_note_daily_vps.sh): VPSでnote補助ツールを実行し、終了ログをSlackへ通知するラッパースクリプト

## 動作確認コマンド

VPSへ入る:

```bash
ssh ubuntu@<VPSのIP>
```

Slackへ送らずに文面だけ確認:

```bash
/usr/bin/python3 /home/ubuntu/f_tools/slack_test.py --dry-run
```

今すぐSlackへ送信:

```bash
/usr/bin/python3 /home/ubuntu/f_tools/slack_test.py
```

ログ確認:

```bash
tail -n 50 /home/ubuntu/f_tools/logs/morning_market_check.log
tail -n 50 /home/ubuntu/f_tools/logs/cron.log
```

cron確認:

```bash
crontab -l
systemctl is-active cron
timedatectl
```

## Substack補助ツール

添付されていた「いいね・フォロー」ツールは、以下の安全化を入れた版を作成しました。

- 初期状態はdry-runで、クリックしない
- `Subscribe` / `登録` / `購読` は自動クリック対象から除外
- `Follow` / `フォロー` だけ対象
- `--execute` を付けた時だけクリック
- `--execute` だけなら1件ごとに確認
- `--execute --yes` の時だけ確認なし
- 重複防止用の履歴を保存
- エラーをログへ出力
- 実行時は投稿順、スクロール量、待機時間をランダム化
- 1回の自動実行は合計6アクションまで

候補確認だけ:

```bash
python3 outputs/substack_like_follow_safe.py
```

確認しながら実行:

```bash
python3 outputs/substack_like_follow_safe.py --execute
```

### VPSで毎日12:00の自動起動

VPSのcronで、毎日12:00 JSTに以下を実行します。Macの電源が入っていなくても動く構成です。

```text
/home/ubuntu/f_tools/run_substack_daily_vps.sh
```

自動実行時の内容:

```bash
/usr/bin/python3 /home/ubuntu/f_tools/substack_like_follow_safe.py \
  --execute \
  --yes \
  --max-actions 6 \
  --max-likes 6 \
  --max-follows 2 \
  --min-wait 30 \
  --max-wait 120 \
  --view-min-wait 8 \
  --view-max-wait 24 \
  --scrolls 8
```

cron:

```cron
0 12 * * * /bin/bash /home/ubuntu/f_tools/run_substack_daily_vps.sh >> /home/ubuntu/f_tools/logs/substack_daily_cron.log 2>&1
```

ログ:

```text
/home/ubuntu/f_tools/logs/substack_daily.log
/home/ubuntu/f_tools/logs/substack_daily_last.log
/home/ubuntu/f_tools/logs/substack_daily_cron.log
```

終了時にはSlackへ以下を通知します。

```text
Substackいいね・フォロー自動実行 終了
exit_status
ログ末尾
```

## X補助ツール

添付されていたX用ツール `auto_like_v11.2.1_hotfix.py` は、1回の起動で必ず止まる上限を追加した版を作成しました。

追加した上限:

- `--max-actions`: 1回の合計アクション数
- `--max-likes`: 1回のいいね数
- `--max-follows`: 1回のフォロー数
- `--max-unfollows`: 1回のアンフォロー数
- `--max-runtime-minutes`: 1回の実行時間
- headlessでログアウト画面が出た場合は、成功扱いせず終了
- 終了時に合計、いいね、フォロー、アンフォロー数をログへ出力

VPS上の配置:

```text
/home/ubuntu/f_tools/auto_like_v11_2_1_limited.py
/home/ubuntu/f_tools/run_x_daily_vps.sh
/home/ubuntu/prometheus/config.ini
```

VPSにはPlaywrightとChromiumを導入済みです。

現在のX自動実行コマンド:

```bash
/usr/bin/python3 /home/ubuntu/f_tools/auto_like_v11_2_1_limited.py \
  --timeline \
  --headless \
  --config /home/ubuntu/prometheus/config.ini \
  --max-actions 12 \
  --max-likes 10 \
  --max-follows 2 \
  --max-unfollows 0 \
  --max-runtime-minutes 45
```

VPSで確認済み:

- スクリプトの構文チェック: OK
- Playwright / Chromium起動: OK
- X未ログイン時の停止: OK
- Slack終了通知: OK

ログイン状態:

- Mac側のXログイン状態からVPS用の `x_storage_state.json` を作成
- VPSの `/home/ubuntu/prometheus/x_storage_state.json` に配置
- 0件テストで `ログイン済み` とツイート検出を確認
- `x_storage_state.json` は秘密情報扱いなのでGitHubには保存しない

cron:

```cron
0 7 * * * /bin/bash /home/ubuntu/f_tools/run_x_daily_vps.sh >> /home/ubuntu/f_tools/logs/x_daily_cron.log 2>&1
30 12 * * * /bin/bash /home/ubuntu/f_tools/run_x_daily_vps.sh >> /home/ubuntu/f_tools/logs/x_daily_cron.log 2>&1
0 19 * * * /bin/bash /home/ubuntu/f_tools/run_x_daily_vps.sh >> /home/ubuntu/f_tools/logs/x_daily_cron.log 2>&1
```

Xは朝7:00、昼12:30、夕19:00の3回稼働です。Substackの12:00実行とは30分ずらしています。

0件テスト:

```bash
X_MAX_ACTIONS=0 X_MAX_LIKES=0 X_MAX_FOLLOWS=0 X_MAX_UNFOLLOWS=0 /bin/bash /home/ubuntu/f_tools/run_x_daily_vps.sh
```

## note補助ツール

noteの `スキ` と `フォロー` を対象にした補助ツールを作成しました。

追加した上限:

- `--max-actions`: 1回の合計アクション数
- `--max-likes`: 1回のスキ数
- `--max-follows`: 1回のフォロー数
- `--keywords`: 対象キーワードCSV。空文字ならキーワードで絞らない
- `--exclude-keywords`: 除外キーワードCSV

VPS上の配置:

```text
/home/ubuntu/f_tools/note_suki_follow_safe.py
/home/ubuntu/f_tools/run_note_daily_vps.sh
```

VPSで確認済み:

- スクリプトの構文チェック: OK
- Playwright / Chromium起動: OK
- note公開ページの巡回: OK
- スキ / フォロー候補検出: OK
- noteログイン状態のVPS移行: OK
- スキ1件の実行: OK
- フォロー1件の実行: OK
- Slack終了通知: OK

未完了:

- cron登録

ログイン状態:

- Mac側のnoteログイン状態からVPS用の `note_storage_state.json` を作成
- VPSの `/home/ubuntu/prometheus/note_storage_state.json` に配置
- 0件テストで候補検出を確認
- `note_storage_state.json` は秘密情報扱いなのでGitHubには保存しない

0件テスト:

```bash
NOTE_MAX_ACTIONS=0 NOTE_MAX_LIKES=0 NOTE_MAX_FOLLOWS=0 /bin/bash /home/ubuntu/f_tools/run_note_daily_vps.sh
```

## 注意

- `.env`、SSH鍵、ログ、実行履歴はGitHubへ保存しない設定です。
- VPS上の`.env`にはSlack Webhook URLとOpenAI APIキーが入っています。
- このリポジトリには秘密情報を入れない方針です。
- XのログインCookieやstorage_stateは秘密情報扱いです。GitHubには保存しません。
- noteのログインCookieやstorage_stateも秘密情報扱いです。GitHubには保存しません。
