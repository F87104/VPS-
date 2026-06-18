# Codex VPS移行レポート

作成日: 2026-06-19  
対象VPS: `ubuntu@49.212.137.39` / `os3-322-50785.vs.sakura.ne.jp`  
本番フォルダ: `/home/ubuntu/f_tools/`

## 結論

自宅PCの電源を切っても運用を続けたい場合、VPS側に寄せるべきものは大きく2つです。

1. Python自動化、Slack通知、cron、SocialDog/note/X/Substackの実行環境
2. GitHubと同期できる作業ディレクトリ

現在のVPSは、Python自動化サーバーとしてはすでに動いています。朝Slack、X、Substack、note、SocialDog下書き保存のcronも入っています。

ただし、VPS上にはまだCodex CLI本体、npm、GitHub用のgit設定、Gitリポジトリのチェックアウトがありません。つまり「Python自動化はVPSで稼働中」「Codex作業ホストとしては未完成」という状態です。

OpenAI公式Codexマニュアル上では、iPadのChatGPTアプリからCodexを遠隔操作する場合、接続先ホストはMacまたはWindowsのCodex Appホストが前提です。ホストPCがスリープ・オフライン・Codex終了状態になると遠隔操作は止まります。VPSを使う場合は、Codex AppからSSHホストとしてVPSを追加する方式、またはCodex Web/CloudとGitHubを中心にした方式が現実的です。

おすすめは次の構成です。

```text
iPad
↓
ChatGPT / Codex Web
↓
GitHub
↓
さくらVPSで git pull / cron / Python 実行
↓
Slack通知
```

VPS上で直接Codex CLIを使う構成も可能ですが、その場合はVPSへCodex CLIを入れて `codex login --device-auth` などで認証する必要があります。

## STEP1 現状分析

### 1. このCodexセッションから見えるローカル環境

注意: ユーザーさんの説明では「自宅Windows PC」とありますが、このセッションから実際に確認できたローカル環境はmacOSです。Windows本体のOS情報はこのセッションから直接確認できません。

| 項目 | 確認結果 |
| --- | --- |
| OS | macOS 15.6.1 |
| カーネル | Darwin 24.6.0 arm64 |
| Python | Python 3.9.6 |
| pip | pip 21.2.4 |
| Git | Apple Git 2.39.5 |
| Codex CLI | `codex-cli 0.138.0-alpha.7` |
| Codex実体 | `/Applications/Codex.app/Contents/Resources/codex` |
| 作業リポジトリ | `/Users/asamifujita/Documents/Codex/2026-06-07/vps-ubuntu-24-04-slack-vps` |
| GitHub remote | `https://github.com/F87104/VPS-.git` |
| branch | `main` |
| HEAD | `38c4d90` |

ローカルのPython主要ライブラリ:

- `openai==2.15.0`
- `requests==2.32.5`
- `python-dotenv==1.2.1`
- `feedparser==6.0.12`
- `beautifulsoup4==4.14.3`
- `playwright==1.57.0`
- `selenium==4.36.0`
- `pandas==2.3.0`
- `numpy==2.0.2`
- `tweepy==4.16.0`

### 2. さくらVPS環境

| 項目 | 確認結果 |
| --- | --- |
| OS | Ubuntu 24.04.4 LTS |
| ホスト名 | `os3-322-50785` |
| カーネル | Linux 6.8.0-36-generic |
| CPU | 2 vCPU / Intel Core Processor Broadwell |
| メモリ | 約 1GB |
| Swap | なし |
| ディスク | 50GB中 約40GB空き |
| タイムゾーン | Asia/Tokyo JST |
| Python | Python 3.12.3 |
| pip | pip 24.0 |
| Git | 2.43.0 |
| Node.js | v18.19.1 |
| npm | 未導入 |
| Codex CLI | 未導入 |
| cron | active |

VPSに入っている主要パッケージ:

- `cron`
- `git`
- `xvfb`
- `openbox`
- `x11vnc`
- `novnc`
- `websockify`

VPSのPython主要ライブラリ:

- `openai==2.41.0`
- `requests==2.34.2`
- `python-dotenv==1.2.2`
- `yfinance==1.4.1`
- `feedparser==6.0.12`
- `beautifulsoup4==4.14.3`
- `playwright==1.60.0`
- `selenium==4.44.0`
- `pandas==3.0.3`
- `numpy==2.4.6`

import確認:

```text
core imports ok
playwright import ok
```

### 3. GitHub連携状況

ローカル側:

```text
origin https://github.com/F87104/VPS-.git
branch main
```

VPS側:

- `/home/ubuntu` 配下にGitリポジトリのチェックアウトは見つかりませんでした。
- `/home/ubuntu/.gitconfig` は未作成でした。
- `gh` GitHub CLI は未導入でした。
- `/home/ubuntu/.ssh/authorized_keys` はあり、こちらからのSSH接続はできています。

つまり、現在のVPSは「GitHubからcloneしてpullする構成」ではなく、必要ファイルを `/home/ubuntu/f_tools/` に直接配置している状態です。

### 4. Slack連携状況

VPSの `/home/ubuntu/f_tools/.env` は存在します。値は表示せず、キー名だけ確認しました。

```text
SLACK_WEBHOOK_URL=<set>
OPENAI_API_KEY=<set>
OPENAI_MODEL=<set>
ERROR_NOTIFY_SLACK=<set>
```

Slack通知用スクリプト:

- `/home/ubuntu/f_tools/slack_test.py`
- `/home/ubuntu/f_tools/notify_slack_summary.py`

ログ:

- `/home/ubuntu/f_tools/logs/cron.log`
- `/home/ubuntu/f_tools/logs/morning_market_check.log`
- `/home/ubuntu/f_tools/logs/substack_daily_last.log`
- `/home/ubuntu/f_tools/logs/x_daily_last.log`
- `/home/ubuntu/f_tools/logs/note_daily_last.log`
- `/home/ubuntu/f_tools/logs/socialdog_daily_drafts_last.log`

### 5. 実行中プロジェクト一覧

VPS本番フォルダ `/home/ubuntu/f_tools/` にある主な実行ファイル:

| 種類 | ファイル |
| --- | --- |
| 朝Slack通知 | `slack_test.py` / `morning_market_check.py` |
| Slack終了通知 | `notify_slack_summary.py` |
| Substack | `substack_like_follow_safe.py` / `run_substack_daily_vps.sh` |
| X | `auto_like_v11_2_1_limited.py` / `run_x_daily_vps.sh` |
| note | `note_suki_follow_safe.py` / `run_note_daily_vps.sh` |
| SocialDog | `socialdog_generate_daily_posts.py` / `socialdog_draft_safe.py` / `run_socialdog_daily_drafts_vps.sh` |
| 仮想デスクトップ | `start_socialdog_login_desktop_vps.sh` |

cron設定:

```cron
0 6 * * * /usr/bin/python3 /home/ubuntu/f_tools/slack_test.py >> /home/ubuntu/f_tools/logs/cron.log 2>&1
0 12 * * * /bin/bash /home/ubuntu/f_tools/run_substack_daily_vps.sh >> /home/ubuntu/f_tools/logs/substack_daily_cron.log 2>&1
0 7 * * * /bin/bash /home/ubuntu/f_tools/run_x_daily_vps.sh >> /home/ubuntu/f_tools/logs/x_daily_cron.log 2>&1
30 12 * * * /bin/bash /home/ubuntu/f_tools/run_x_daily_vps.sh >> /home/ubuntu/f_tools/logs/x_daily_cron.log 2>&1
0 19 * * * /bin/bash /home/ubuntu/f_tools/run_x_daily_vps.sh >> /home/ubuntu/f_tools/logs/x_daily_cron.log 2>&1
0 8 * * * /bin/bash /home/ubuntu/f_tools/run_note_daily_vps.sh >> /home/ubuntu/f_tools/logs/note_daily_cron.log 2>&1
0 13 * * * /bin/bash /home/ubuntu/f_tools/run_note_daily_vps.sh >> /home/ubuntu/f_tools/logs/note_daily_cron.log 2>&1
0 20 * * * /bin/bash /home/ubuntu/f_tools/run_note_daily_vps.sh >> /home/ubuntu/f_tools/logs/note_daily_cron.log 2>&1
40 5 * * * /bin/bash /home/ubuntu/f_tools/run_socialdog_daily_drafts_vps.sh >> /home/ubuntu/f_tools/logs/socialdog_daily_drafts_cron.log 2>&1
```

## 問題点

1. VPSのメモリが約1GBでSwapなし
   - Slack通知だけなら動きます。
   - Playwright、仮想デスクトップ、Codex CLIを同時に使うには少なめです。

2. VPSにCodex CLIが未導入
   - `which codex` で未検出でした。
   - iPadからPCなしでCodex作業をしたい場合、Codex Web/CloudまたはVPS上のCodex CLI整備が必要です。

3. VPSにnpmが未導入
   - Codex CLIや一部ツールでNode/npmが必要になる可能性があります。

4. VPSにGitHub作業リポジトリがない
   - 現在は `/home/ubuntu/f_tools/` に本番ファイルが直置きです。
   - 今後は `~/VPS-` にGitHub repoをcloneし、そこから本番へ反映する形が安全です。

5. venvが整理されていない
   - `/home/ubuntu/f_tools/venv` と `x_venv` はありますが、pipが入っていません。
   - 現在はsystem Pythonにライブラリが入っていて、cronも `/usr/bin/python3` を使っています。
   - 移行後は `/home/ubuntu/f_tools/.venv/bin/python` に寄せるのがおすすめです。

## STEP2 VPS移行手順書

### VPS推奨スペック

最低ライン:

- Ubuntu 24.04 LTS
- 2 vCPU
- メモリ 2GB
- SSD 50GB

おすすめ:

- Ubuntu 24.04 LTS
- 4 vCPU
- メモリ 4GB以上
- SSD 80GB以上

理由:

- Python + Slack通知だけなら軽いです。
- Playwright、SocialDog、note、X、仮想デスクトップ、Codex CLIを同じVPSで使うならメモリ4GB以上が安心です。
- 現在のVPSは約1GBなので、継続利用するならSwap 2GB追加をおすすめします。

### 必要パッケージ

```bash
sudo apt update
sudo apt install -y git curl ca-certificates jq build-essential \
  python3 python3-venv python3-pip \
  cron tmux ufw \
  nodejs npm \
  xvfb openbox x11vnc novnc websockify
```

### SSH設定

自分のPCやiPad用SSHアプリから入れる状態にします。

入力するもの:

```bash
ssh ubuntu@49.212.137.39
```

成功すると:

```text
ubuntu@os3-322-50785:~$
```

のような表示になります。

安全運用では、パスワードログインよりSSH鍵ログインを使います。`~/.ssh/authorized_keys` に公開鍵を入れます。秘密鍵はGitHubやチャットに貼りません。

### GitHub設定

VPSにGitHubリポジトリを置く場合:

```bash
cd /home/ubuntu
git clone https://github.com/F87104/VPS-.git VPS-
cd VPS-
git status
```

成功すると:

```text
On branch main
```

のように表示されます。

GitHubへpushもVPSから行いたい場合は、どちらかを設定します。

- HTTPS + GitHub Personal Access Token
- SSH鍵 + GitHubに公開鍵登録

初心者向けには、まずHTTPS + Personal Access Tokenの方が分かりやすいです。長期運用ではSSH鍵がおすすめです。

### Python設定

本番用フォルダ:

```bash
mkdir -p /home/ubuntu/f_tools/logs
cd /home/ubuntu/f_tools
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

成功すると:

```text
Successfully installed ...
```

と表示されます。

### Slack設定

`/home/ubuntu/f_tools/.env` を作ります。

```bash
nano /home/ubuntu/f_tools/.env
```

貼る内容:

```env
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxxxx
OPENAI_API_KEY=sk-xxxxx
OPENAI_MODEL=gpt-5.5
ERROR_NOTIFY_SLACK=true
```

保存方法:

1. `Ctrl + O`
2. Enter
3. `Ctrl + X`

成功確認:

```bash
/home/ubuntu/f_tools/.venv/bin/python /home/ubuntu/f_tools/slack_test.py --dry-run
```

Slackへ送る確認:

```bash
/home/ubuntu/f_tools/.venv/bin/python /home/ubuntu/f_tools/slack_test.py
```

Slackに投稿が出れば成功です。

### Codex設定

選択肢は3つあります。

#### 方法A: Codex Web/Cloud + GitHub + VPS

PCを不要にしたいなら、この方法が一番きれいです。

```text
iPad
↓
ChatGPT / Codex Web
↓
GitHub repoを編集
↓
VPSで git pull
↓
cron / Python / Slack
```

良い点:

- 自宅PCがオフでも使えます。
- iPadからChatGPT経由で作業できます。
- VPSは自動実行と本番運用に集中できます。

注意点:

- VPSの中のファイルを直接いじるのではなく、GitHub経由で反映する運用になります。

#### 方法B: Codex AppからVPSをSSHホストとして追加

OpenAI公式マニュアルでは、Codex AppからSSHホストを追加し、リモートのファイルシステムとshellに対して作業できます。

ただし、iPadのChatGPTアプリから遠隔操作する場合、接続元になるCodex AppホストはMacまたはWindowsで、起動中・オンライン・同じアカウントでサインイン済みである必要があります。自宅PCを完全に不要にする目的とは少しずれます。

#### 方法C: VPS上でCodex CLIを直接使う

VPSへCodex CLIを入れて、SSHやWebターミナルから使う方法です。

```bash
codex login --device-auth
codex doctor
codex
```

良い点:

- VPSだけで完結できます。
- iPadからSSHアプリでVPSへ入れば使えます。

注意点:

- ChatGPTアプリのCodex遠隔操作と同じ体験ではありません。
- 認証情報 `~/.codex/auth.json` はパスワード同等なので、絶対にGitHubへ入れません。
- 公開ポートでCodex app-serverを外に出す構成は避けます。使うならSSHまたはVPN経由にします。

## STEP3 自動化スクリプト

作成済み:

- `outputs/setup_codex_vps_host.sh`
- `outputs/cron_vps_template.txt`
- `outputs/requirements.txt`

VPSへ貼る基本手順:

```bash
cd /home/ubuntu
git clone https://github.com/F87104/VPS-.git VPS-
cd VPS-
bash outputs/setup_codex_vps_host.sh
```

すでにclone済みの場合:

```bash
cd /home/ubuntu/VPS-
git pull
bash outputs/setup_codex_vps_host.sh
```

cronをテンプレートで入れる場合:

```bash
crontab outputs/cron_vps_template.txt
crontab -l
```

成功すると、朝6:00や5:40などの行が表示されます。

## STEP4 完成イメージ

最終構成:

```text
iPad
↓
ChatGPT / Codex Web
↓
GitHub: https://github.com/F87104/VPS-.git
↓
さくらVPS: Ubuntu 24.04
↓
/home/ubuntu/f_tools/
↓
Python 3.12 + cron
↓
Slack Incoming Webhook
```

運用の流れ:

1. iPadからChatGPT / Codexでコードや文体ルールを直す
2. GitHubへ保存する
3. VPSで `git pull` する
4. `/home/ubuntu/f_tools/` へ反映する
5. cronが決まった時間に自動実行する
6. Slackへ結果や終了ログが届く

## 今すぐやるべき順番

1. VPSのメモリ増強またはSwap追加
2. VPSにGitHub repoをclone
3. `/home/ubuntu/f_tools/.venv` を作り直す
4. cronのPythonパスを `.venv/bin/python` に寄せる
5. Codex Web/CloudでGitHub連携を使うか、VPSにCodex CLIを入れるか決める
6. 決めた方式で1回だけ認証する
7. `slack_test.py --dry-run` とSlack実送信で確認する

## 参照した公式情報

- OpenAI Codex Manual: https://developers.openai.com/codex/codex-manual.md
- OpenAI Codex Quickstart: https://developers.openai.com/codex/quickstart

