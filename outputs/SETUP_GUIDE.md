# さくらVPS Ubuntu 24.04: 朝6時Slack相場通知セットアップ

この手順は、VPSのターミナルへ上から順番に貼れば動く形です。

## 作るフォルダ構成

```text
~/morning-slack/
├── app/
│   └── morning_market_check.py
├── logs/
│   ├── morning_market_check.log
│   └── cron.log
├── requirements.txt
├── .env
└── venv/
```

## 1. VPSへログイン

自分のPCのターミナルで、いつもの方法でVPSへ入ります。

```bash
ssh ユーザー名@VPSのIPアドレス
```

ログインできたら、画面の左側に `ユーザー名@サーバー名` のような表示が出ます。

## 2. Ubuntuの時刻を日本時間にする

cronはサーバーの時刻で動きます。朝6時にしたいので、まず日本時間へ合わせます。

```bash
sudo timedatectl set-timezone Asia/Tokyo
timedatectl
```

成功の目印:

```text
Time zone: Asia/Tokyo
```

## 3. 必要なものをインストール

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip cron
sudo systemctl enable cron
sudo systemctl start cron
```

成功の目印:

```text
Setting up ...
```

最後にエラーが出なければOKです。

## 4. フォルダを作る

```bash
mkdir -p ~/morning-slack/app ~/morning-slack/logs
cd ~/morning-slack
```

今いる場所を確認します。

```bash
pwd
```

成功の目印:

```text
/home/ユーザー名/morning-slack
```

## 5. requirements.txtを作る

```bash
nano requirements.txt
```

開いた画面に、以下を貼り付けます。

```text
openai>=1.99.0
requests>=2.32.0
python-dotenv>=1.0.1
yfinance>=0.2.54
feedparser>=6.0.11
```

保存方法:

1. `Ctrl + O`
2. `Enter`
3. `Ctrl + X`

## 6. Python仮想環境を作ってライブラリを入れる

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

成功の目印:

```text
Successfully installed ...
```

## 7. .envを作る

`.env` は秘密情報を置くファイルです。Slack Webhook URLとOpenAI APIキーをここへ入れます。

```bash
nano .env
```

以下を貼り付けます。`xxxxx` の部分だけ自分の値に変えてください。

```bash
SLACK_WEBHOOK_URL=YOUR_SLACK_WEBHOOK_URL
OPENAI_API_KEY=YOUR_OPENAI_API_KEY
OPENAI_MODEL=gpt-5.5
ERROR_NOTIFY_SLACK=true
```

保存方法:

1. `Ctrl + O`
2. `Enter`
3. `Ctrl + X`

確認コマンド:

```bash
ls -la
```

成功の目印:

```text
.env
requirements.txt
app
logs
venv
```

## 8. Pythonコードを貼る

```bash
nano app/morning_market_check.py
```

`morning_market_check.py` の全文を貼り付けます。

保存方法:

1. `Ctrl + O`
2. `Enter`
3. `Ctrl + X`

実行できるようにします。

```bash
chmod +x app/morning_market_check.py
```

## 9. Slackだけテスト

```bash
cd ~/morning-slack
source venv/bin/activate
python app/morning_market_check.py --test-slack
```

成功の目印:

```text
Slackテスト送信に成功しました。
```

Slackに `✅ Slackテスト成功...` が届けばOKです。

## 10. OpenAI生成込みでテスト

まずSlackへ送らず、画面だけに表示します。

```bash
cd ~/morning-slack
source venv/bin/activate
python app/morning_market_check.py --dry-run
```

成功の目印:

```text
🌅 朝イチ相場CHECK
📊 主要指数
...
☕ Fの朝のつぶやき案
```

問題なければ、実際にSlackへ送ります。

```bash
python app/morning_market_check.py
```

成功の目印:

```text
Slack送信に成功しました。
```

Slackへ朝イチ相場CHECKが届けばOKです。

## 11. cronを設定する

以下をそのまま貼ります。

```bash
CRON_CMD="0 6 * * * cd $HOME/morning-slack && $HOME/morning-slack/venv/bin/python $HOME/morning-slack/app/morning_market_check.py >> $HOME/morning-slack/logs/cron.log 2>&1"
(crontab -l 2>/dev/null | grep -v 'morning_market_check.py'; echo "$CRON_CMD") | crontab -
crontab -l
```

成功の目印:

```text
0 6 * * * cd /home/ユーザー名/morning-slack && /home/ユーザー名/morning-slack/venv/bin/python ...
```

これで毎朝6:00に実行されます。

## 12. ログ確認

通常ログ:

```bash
tail -n 50 ~/morning-slack/logs/morning_market_check.log
```

cronログ:

```bash
tail -n 50 ~/morning-slack/logs/cron.log
```

成功の目印:

```text
Morning market check sent successfully
```

エラー時は `Morning market check failed` と理由が出ます。

## 13. 再起動後の確認

cronは再起動後も残ります。念のため以下を確認します。

```bash
sudo systemctl status cron
crontab -l
```

成功の目印:

```text
active (running)
0 6 * * *
```

## API取得方法

### Slack Incoming Webhook

1. `https://api.slack.com/apps` を開く
2. 自分のSlackアプリを選ぶ
3. 左メニューの `Incoming Webhooks`
4. `Activate Incoming Webhooks` をON
5. `Add New Webhook to Workspace`
6. 投稿したいチャンネルを選ぶ
7. `https://hooks.slack.com/services/...` を `.env` の `SLACK_WEBHOOK_URL` に貼る

### OpenAI API

1. `https://platform.openai.com/api-keys` を開く
2. APIキーを作成する
3. 作成されたAPIキーをコピーする
4. `.env` の `OPENAI_API_KEY` に貼る

### 相場データとニュース

- 相場データ: `yfinance` で取得します。追加のAPIキーは不要です。
- ニュース: Google News RSSを取得します。追加のAPIキーは不要です。
- 文章生成: OpenAI APIを使います。OpenAI APIキーが必要です。

## よくあるエラー

### Slackに届かない

```bash
python ~/morning-slack/app/morning_market_check.py --test-slack
```

- `status=200, response=ok` ならSlack URLはOKです。
- `invalid_payload` はJSON形式の問題です。
- `404` や `no_service` はWebhook URLが古い可能性があります。

### OpenAIでエラーになる

```bash
tail -n 50 ~/morning-slack/logs/morning_market_check.log
```

- `OPENAI_API_KEY` が空ではないか確認します。
- `model not found` が出た場合は、`.env` の `OPENAI_MODEL` を自分のアカウントで使えるモデル名へ変更します。

### cronでは動かないが手動では動く

```bash
tail -n 50 ~/morning-slack/logs/cron.log
```

ここにエラーが出ます。多い原因は、cronのパス間違いか `.env` の保存場所間違いです。
