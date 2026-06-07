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

候補確認だけ:

```bash
python3 outputs/substack_like_follow_safe.py
```

確認しながら実行:

```bash
python3 outputs/substack_like_follow_safe.py --execute
```

## 注意

- `.env`、SSH鍵、ログ、実行履歴はGitHubへ保存しない設定です。
- VPS上の`.env`にはSlack Webhook URLとOpenAI APIキーが入っています。
- このリポジトリには秘密情報を入れない方針です。
