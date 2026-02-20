# SUUMO新着通知モニター

SUUMOで検索条件に合う物件が新着で出たら、**LINE / Slack / メール**で通知するツールです。

## 仕組み

```
10分ごと (cron/launchd)
        ↓
  SUUMOをスクレイプ
        ↓
  既知のIDと差分比較 (SQLite)
        ↓
  新着があればLINE + Slack + メールで通知
        ↓
  DBに保存 (次回以降は重複通知しない)
```

## セットアップ

### 1. 依存ライブラリのインストール

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 設定ファイルの作成

```bash
cp .env.example .env
```

---

## 通知チャンネルの設定 (1つ以上設定)

### LINE Messaging API (無料 / 200通/月)

> LINE Notify は2025年3月末にサービス終了しました。代わりにMessaging APIを使います。

1. https://developers.line.biz/ にログイン
2. 「Providers」→「Create a provider」→ 名前を入力して作成
3. 「Create a Messaging API channel」→ 必要事項を入力して作成
4. 「Messaging API設定」タブ → 「チャンネルアクセストークン」→「発行」→ コピー
5. `.env` に設定:
   ```
   LINE_CHANNEL_ACCESS_TOKEN=コピーしたトークン
   ```
6. LINEアプリでそのBotをQRコードから友だち追加
7. BotにLINEで何かメッセージを送る
8. 以下のコマンドでユーザーIDを取得:
   ```bash
   curl -H "Authorization: Bearer {LINE_CHANNEL_ACCESS_TOKEN}" \
        https://api.line.me/v2/bot/followers/ids
   # → {"userIds": ["Uxxxxxxxxxx..."]} の Uで始まる文字列をコピー
   ```
9. `.env` に設定:
   ```
   LINE_USER_ID=Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

---

### Slack Incoming Webhook (無料 / 無制限)

1. https://api.slack.com/apps → 「Create New App」→「From scratch」
2. アプリ名 (例: `SUUMO Monitor`) と通知先のWorkspaceを選択 → 「Create App」
3. 左メニュー「Incoming Webhooks」→ 右上トグルで「Activate Incoming Webhooks」をON
4. 「Add New Webhook to Workspace」→ 通知先チャンネルを選択 → 「許可する」
5. 表示されたWebhook URL (`https://hooks.slack.com/services/...`) をコピー
6. `.env` に設定:
   ```
   SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../xxx
   ```

---

### Gmail メール (無料 / 無制限)

> 通常のGmailパスワードではなく「アプリパスワード」が必要です。

1. Googleアカウントで **2段階認証** を有効化 (設定 → セキュリティ)
2. https://myaccount.google.com/apppasswords を開く
3. 「アプリを選択」→「メール」、「デバイスを選択」→「Macintosh」→「生成」
4. 表示された16文字のパスワードをコピー
5. `.env` に設定:
   ```
   EMAIL_ENABLED=true
   SMTP_USERNAME=your.email@gmail.com
   SMTP_PASSWORD=xxxx xxxx xxxx xxxx  # 生成した16文字
   EMAIL_FROM=your.email@gmail.com
   EMAIL_TO=recipient@example.com
   ```

---

## SUUMO検索URLの取得

1. [suumo.jp](https://suumo.jp) で希望の条件 (エリア・家賃・間取りなど) を設定して物件一覧を表示する
2. ブラウザのアドレスバーにあるURLをそのままコピーして `.env` の `SUUMO_SEARCH_URL` に貼り付ける

---

## 動作確認

```bash
source .venv/bin/activate

# ドライラン: 通知は送らずスクレイピングのみ確認
python -m suumo_monitor.monitor --dry-run
# → "スクレイプ完了: 合計 XX 件" と表示されればOK

# 実際に通知を送信
python -m suumo_monitor.monitor
# → LINE/Slack/メールに通知が届くことを確認
# → 2回目実行で "新着物件なし。" と出れば重複通知防止も正常
```

---

## 定期実行の設定

### Mac launchd (推奨)

```bash
# plistをLaunchAgentsにコピー
cp scripts/com.suumo.monitor.plist ~/Library/LaunchAgents/

# 登録して即時起動
launchctl load ~/Library/LaunchAgents/com.suumo.monitor.plist

# 動作確認
launchctl list | grep suumo
```

停止する場合:
```bash
launchctl unload ~/Library/LaunchAgents/com.suumo.monitor.plist
```

**実行間隔の変更:** `scripts/com.suumo.monitor.plist` の `StartInterval` の値を変更 (秒単位):
- 5分: `300`
- 10分: `600` (デフォルト)
- 15分: `900`

### cron (代替)

```bash
crontab -e
# 以下を追記 (10分ごと):
*/10 * * * * /Users/morisakura/dev/suumo/scripts/run_monitor.sh >> /Users/morisakura/dev/suumo/data/cron.log 2>&1
```

---

## ファイル構成

```
suumo/
├── suumo_monitor/
│   ├── config.py       # 設定読み込み
│   ├── scraper.py      # SUUMOスクレイピング
│   ├── storage.py      # SQLite管理
│   ├── notifier.py     # LINE / Slack / メール通知
│   └── monitor.py      # メイン実行ロジック
├── scripts/
│   ├── run_monitor.sh             # cron/launchd用ラッパー
│   └── com.suumo.monitor.plist    # macOS launchd設定 (10分間隔)
├── data/                          # DB・ログ (gitignore済み)
├── .env.example                   # 設定テンプレート
└── requirements.txt
```

---

## トラブルシューティング

**物件が1件も取得できない:**
- SUUMOのHTML構造が変更された可能性があります
- `suumo_monitor/scraper.py` の `SELECTORS` 辞書を確認・修正してください

**LINEに届かない:**
- `LINE_CHANNEL_ACCESS_TOKEN` と `LINE_USER_ID` の両方が設定されているか確認
- LINE Developers ConsoleでMessaging APIチャンネルが「公開」状態になっているか確認
- `curl -H "Authorization: Bearer {token}" https://api.line.me/v2/bot/info` でBot情報が取得できるか確認

**Slackに届かない:**
- Webhook URLが正しいか確認 (`https://hooks.slack.com/services/` で始まる)
- `curl -X POST -d '{"text":"test"}' {WEBHOOK_URL}` で直接テスト送信して確認

**Gmailが送れない:**
- `SMTP_PASSWORD` に通常のGmailパスワードではなく**アプリパスワード**を設定しているか確認
- Googleアカウントの2段階認証が有効か確認

**ログの確認:**
```bash
tail -f data/monitor.log    # 実行ログ
tail -f data/launchd.log    # launchd出力
```
