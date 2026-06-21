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
| 朝イチCHECKニュース鮮度フィルタ | 完了 |
| 朝イチCHECKのチェックリスト型フォーマット | 完了 |
| 朝イチCHECKのコピー用コードブロック追加 | 完了 |
| 朝イチCHECKの軽い実況表現を抑えた深みある文体化 | 完了 |
| 重要指標カレンダー取得 | 完了 |
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
| noteのcron自動起動 | 8:00 / 13:00 / 20:00に設定済み |
| X / Substack / noteの件数上限増量 | X・noteを2026-06-21に再増量 |
| SocialDog下書き保存テスト | 完了 |
| 投資家FのX文体研究 | 完了 |
| SocialDog VPS仮想デスクトップログイン | 完了 |
| SocialDog朝昼夕3投稿の自動下書き保存 | 毎朝5:40 JSTに設定済み |
| SocialDog投資家F文体完全再現ルール | 完了 |
| 投資家F文体の再分析 / 品質改善 | 完了 |
| SocialDog投稿の投資助言・ポジション表現除外 | 完了 |
| 朝投稿の注目ニュース箇条書き化 | 完了 |
| 昼・夕タイトルのランダム化 | 完了 |
| Codex VPS移行調査 / 手順書 / 自動セットアップ案 | 完了 |
| メルカリ割安候補検出MVP | Apple Watch実検索 / CSV保存 / ノイズ除外まで確認済み |
| GitHub push | 完了 |

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

````text
🌅 朝イチ相場CHECK

＼今日のマーケット🌈🐻／
米10年金利とドル円が同じ方向へ走る日は、
NASDAQの買い手が一度ブレーキを踏みやすい朝です🐻
✅ドル円は156円台で推移
✅GOLDは4,115ドル台まで上昇
✅NASDAQは前日比-0.97%で下落

今夜の重要指標
21:30 🇺🇸生産者物価指数（PPI）

私は米10年金利、ドル円、GOLDの順番を並べて見ます。
同じニュースでも、どこが先に反応したかで買い手の本音が出ます😺

投資家Fより💌

📋 コピー用
```
＼今日のマーケット🌈🐻／
米10年金利とドル円が同じ方向へ走る日は、
NASDAQの買い手が一度ブレーキを踏みやすい朝です🐻
✅ドル円は156円台で推移
✅GOLDは4,115ドル台まで上昇
✅NASDAQは前日比-0.97%で下落

今夜の重要指標
21:30 🇺🇸生産者物価指数（PPI）

私は米10年金利、ドル円、GOLDの順番を並べて見ます。
同じニュースでも、どこが先に反応したかで買い手の本音が出ます😺

投資家Fより💌
```
````

重要指標は、みんかぶFXの経済指標カレンダーから今後24時間分を取得し、米国・日本・ユーロ圏などの主要イベントを優先して表示します。

## ニュース鮮度フィルタ

朝イチCHECKは、Google News RSSの検索結果から直近ニュースだけを拾うようにしています。

- 検索条件に `when:2d` を追加
- 公開時刻が36時間より古い記事を除外
- `Yahoo!ファイナンス` の指数ページ、為替レートページ、チャートページを除外
- `Yahoo!フリマ` など相場ニュースではない商品ページを除外
- 為替、金利、FRB、CPI、株、GOLD、NASDAQ、S&P500、NYダウ、日銀、財務相、介入、地政学など相場関連語を含む記事だけ採用
- 同じ見出しの転載記事は重複除外
- OpenAIへ現在日時とニュース公開時刻を渡し、昨日以前の材料を今日の新規材料として扱わせない

## 投資家F文体ルール

現在の生成方針:

- 冒頭の `こんにちは、Fです✨` は入れない
- 一人称は `私`
- ニュース内の固有名詞、数字、仕組みを入れる
- 需給、資金の質、投資家心理へつなげる
- 実体験や直近の行動を想起させる一言を入れる
- 表面的な意味とは逆の可能性を入れる
- F自身のポジション、注文、売買予定は書かない
- 観察、確認、1行メモ、ニュースへの反応など、相場の見方に寄せる
- チェックリストの✅3行はニュースのピックアップ事実だけにする
- ✅にはFの見方、需給の読み、次に見る反応を書かない
- 軽い実況ではなく、固有名詞・数字・資金の動きが見える言葉にする
- `資金が逃げる先` `資金が残った` で止めず、需給の主体まで書く
- 遊び心は入れても1か所まで。意味の薄い比喩より、読者の見方が1つ増える表現を優先する
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
- [outputs/socialdog_draft_safe.py](outputs/socialdog_draft_safe.py): SocialDogへ投稿本文を入れて下書き保存するテスト用スクリプト
- [outputs/socialdog_generate_daily_posts.py](outputs/socialdog_generate_daily_posts.py): 相場・ニュース・急上昇候補から朝昼夕3投稿を生成するスクリプト
- [outputs/run_socialdog_daily_drafts_vps.sh](outputs/run_socialdog_daily_drafts_vps.sh): VPSで毎朝5:40にSocialDog下書き保存を実行するラッパースクリプト
- [outputs/start_socialdog_login_desktop_vps.sh](outputs/start_socialdog_login_desktop_vps.sh): VPSの仮想デスクトップでSocialDogへログインするための補助スクリプト
- [outputs/F_STYLE_GUIDE.md](outputs/F_STYLE_GUIDE.md): X投稿をもとにした投資家Fの文体ガイド
- [outputs/F_STYLE_DEEP_ANALYSIS.md](outputs/F_STYLE_DEEP_ANALYSIS.md): X公開投稿を再分析した文体深掘りメモ
- [outputs/SOCIALDOG_3POST_STRATEGY.md](outputs/SOCIALDOG_3POST_STRATEGY.md): 朝昼夕3投稿の運用方針
- [outputs/CODEX_VPS_MIGRATION_REPORT.md](outputs/CODEX_VPS_MIGRATION_REPORT.md): CodexをVPS中心運用へ移すための現状分析と手順書
- [outputs/setup_codex_vps_host.sh](outputs/setup_codex_vps_host.sh): VPS側のパッケージ、GitHub clone、Python venv、cron準備を行うセットアップ補助スクリプト
- [outputs/cron_vps_template.txt](outputs/cron_vps_template.txt): VPS用cronテンプレート
- [outputs/mercari_deal_finder/](outputs/mercari_deal_finder/): メルカリの割安候補をCSV保存し、条件一致時にSlack通知するMVP

## SocialDog朝昼夕下書き

VPS上でSocialDogに一度ログインし、以下のプロフィールにログイン状態を保存しています。

```text
/home/ubuntu/f_tools/.socialdog_profile
```

毎朝5:40 JSTに、朝・昼・夕の3本を生成してSocialDogの下書きに保存します。Macの電源が入っていなくてもVPS側で動きます。

```cron
40 5 * * * /bin/bash /home/ubuntu/f_tools/run_socialdog_daily_drafts_vps.sh >> /home/ubuntu/f_tools/logs/socialdog_daily_drafts_cron.log 2>&1
```

下書き生成の文体は、以下を優先します。

- 朝は `＼今日の注目ニュース🌈🐻／` から始め、インプレッションを狙える注目ニュースを `✅` で3つ入れる
- 朝のニュースは固有名詞、数字、話題性、意外性を優先する
- 重要指標と時刻を入れる
- 昼は意味のある声かけにし、読者が今日できる小さい行動を入れる
- 昼タイトルは固定せず、`＼午後の一歩メモ🌷🐻／` など複数候補から変える
- 夕はNY前の指標、ニュース、値動きのクセ、学びのメモに寄せる
- 夕タイトルは固定せず、`＼NY前のニュース整理🐻‍❄️🌈／` など複数候補から変える
- 各投稿に絵文字を3〜6個入れる
- 署名は必ず `投資家Fより💌`
- 締めは抽象語で終わらせず、時間・対象・手元の動作が見える言葉にする

禁止している表現:

- F自身のポジション、注文、売買予定
- 読者への投資助言や売買指示に見える文章
- `指値` `ポジション` `ロット` `エントリー` `利確` `損切り` `アラート`
- `どの材料に資金が反応` `1行だけメモします` `明日の自分への貯金` `焦って決めない`

代わりに、`21:30後の最初の15分は米金利・GOLD・NASDAQの順番を見る` のように、対象がはっきりした締めにします。

ログ:

```text
/home/ubuntu/f_tools/logs/socialdog_daily_drafts_last.log
/home/ubuntu/f_tools/logs/socialdog_daily_drafts.log
/home/ubuntu/f_tools/logs/socialdog_daily_drafts_cron.log
```

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
- 1回の自動実行は合計13アクションまで

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
  --max-actions 13 \
  --max-likes 10 \
  --max-follows 3 \
  --min-wait 30 \
  --max-wait 120 \
  --view-min-wait 8 \
  --view-max-wait 24 \
  --scrolls 8
```

現在の1回あたり上限:

- 合計アクション: 13
- いいね: 10
- フォロー: 3

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
  --max-actions 24 \
  --max-likes 20 \
  --max-follows 4 \
  --max-unfollows 0 \
  --max-runtime-minutes 55
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

現在の1回あたり上限:

- 合計アクション: 24
- いいね: 20
- フォロー: 4
- アンフォロー: 0
- 最大実行時間: 55分

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

ログイン状態:

- Mac側のnoteログイン状態からVPS用の `note_storage_state.json` を作成
- VPSの `/home/ubuntu/prometheus/note_storage_state.json` に配置
- 0件テストで候補検出を確認
- `note_storage_state.json` は秘密情報扱いなのでGitHubには保存しない

cron:

```cron
0 8 * * * /bin/bash /home/ubuntu/f_tools/run_note_daily_vps.sh >> /home/ubuntu/f_tools/logs/note_daily_cron.log 2>&1
0 13 * * * /bin/bash /home/ubuntu/f_tools/run_note_daily_vps.sh >> /home/ubuntu/f_tools/logs/note_daily_cron.log 2>&1
0 20 * * * /bin/bash /home/ubuntu/f_tools/run_note_daily_vps.sh >> /home/ubuntu/f_tools/logs/note_daily_cron.log 2>&1
```

noteは朝8:00、昼13:00、夜20:00の3回稼働です。Xの稼働時刻とは少しずらしています。

現在の1回あたり上限:

- 合計アクション: 15
- スキ: 12
- フォロー: 3

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
