"""
notifier.py - LINE Messaging API・Slack Incoming Webhook・SMTPメールによる通知。
"""

import json
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import requests

from .scraper import Listing

logger = logging.getLogger(__name__)

LINE_MESSAGING_API_URL = "https://api.line.me/v2/bot/message/push"
LINE_MAX_MSG_LEN = 5000  # LINE Messaging APIの1メッセージあたりの上限


class LineNotifier:
    """
    LINE Messaging APIでプッシュメッセージを送信する。

    セットアップ:
      1. https://developers.line.biz/ でMessaging APIチャンネルを作成
      2. チャンネルアクセストークンを発行
      3. Botを友だち追加し、LINE_USER_ID を取得
         (curl -H "Authorization: Bearer {token}" https://api.line.me/v2/bot/followers/ids)
    """

    def __init__(self, channel_access_token: str, user_id: str):
        if not channel_access_token:
            raise ValueError("LINE_CHANNEL_ACCESS_TOKEN が設定されていません。")
        if not user_id:
            raise ValueError("LINE_USER_ID が設定されていません。")
        self.user_id = user_id
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {channel_access_token}",
            "Content-Type": "application/json",
        })

    def send(self, message: str) -> bool:
        """テキストメッセージを送信する。成功時はTrue。"""
        if len(message) > LINE_MAX_MSG_LEN:
            message = message[: LINE_MAX_MSG_LEN - 3] + "..."

        payload = {
            "to": self.user_id,
            "messages": [{"type": "text", "text": message}],
        }
        try:
            resp = self.session.post(
                LINE_MESSAGING_API_URL,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=15,
            )
            resp.raise_for_status()
            logger.info("LINE通知を送信しました。")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"LINE通知の送信に失敗しました: {e}")
            return False

    def send_new_listings(self, listings: list[Listing]) -> bool:
        """
        新着物件をLINEに通知する。
        5000文字の制限に対応して複数メッセージに分割する。
        """
        if not listings:
            return True

        header = f"【SUUMO新着】{len(listings)}件\n"
        current_chunk = header
        success = True

        for l in listings:
            entry = f"{l.url}\n"
            if len(current_chunk) + len(entry) > LINE_MAX_MSG_LEN:
                success &= self.send(current_chunk)
                current_chunk = entry
            else:
                current_chunk += entry

        if current_chunk.strip():
            success &= self.send(current_chunk)

        return success


class SlackNotifier:
    """
    Slack Incoming Webhookでメッセージを送信する。

    セットアップ:
      1. https://api.slack.com/apps → 「Create New App」→「From scratch」
      2. 「Incoming Webhooks」→「Activate Incoming Webhooks」をON
      3. 「Add New Webhook to Workspace」→ チャンネル選択 → 「許可する」
      4. 表示されたWebhook URLを SLACK_WEBHOOK_URL に設定
    """

    def __init__(self, webhook_url: str):
        if not webhook_url:
            raise ValueError("SLACK_WEBHOOK_URL が設定されていません。")
        self.webhook_url = webhook_url

    def send(self, text: str) -> bool:
        """テキストメッセージを送信する。mrkdwn記法が使える。"""
        try:
            resp = requests.post(
                self.webhook_url,
                json={"text": text},
                timeout=15,
            )
            resp.raise_for_status()
            logger.info("Slack通知を送信しました。")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Slack通知の送信に失敗しました: {e}")
            return False

    def send_new_listings(self, listings: list[Listing]) -> bool:
        """新着物件をSlackに通知する。mrkdwn形式でリッチに表示する。"""
        if not listings:
            return True

        lines = [f":house: *SUUMO新着* {len(listings)}件"] + [l.url for l in listings]
        return self.send("\n".join(lines))


class EmailNotifier:
    """
    SMTPでHTMLメールを送信する。

    Gmail利用時:
      1. Googleアカウントで2段階認証を有効化
      2. https://myaccount.google.com/apppasswords でアプリパスワードを生成
      3. SMTP_HOST=smtp.gmail.com, SMTP_PORT=587 を設定
    """

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        username: str,
        password: str,
        from_addr: str,
        to_addrs: list[str],
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.from_addr = from_addr
        self.to_addrs = to_addrs

    def send_new_listings(self, listings: list[Listing]) -> bool:
        """新着物件のHTMLメールを送信する。"""
        if not listings:
            return True

        subject = f"[SUUMO] {len(listings)}件の新着物件があります"
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.from_addr
        msg["To"] = ", ".join(self.to_addrs)
        msg.attach(MIMEText(self._build_plain(listings), "plain", "utf-8"))
        msg.attach(MIMEText(self._build_html(listings), "html", "utf-8"))

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as server:
                server.starttls()
                server.login(self.username, self.password)
                server.sendmail(self.from_addr, self.to_addrs, msg.as_bytes())
            logger.info(f"メールを送信しました: {self.to_addrs}")
            return True
        except smtplib.SMTPException as e:
            logger.error(f"メール送信に失敗しました: {e}")
            return False

    @staticmethod
    def _build_html(listings: list[Listing]) -> str:
        links = "".join(f'<li><a href="{l.url}">{l.url}</a></li>' for l in listings)
        return f"""
<html><body>
<h2>SUUMO 新着物件通知 ({len(listings)}件)</h2>
<ul>{links}</ul>
</body></html>"""

    @staticmethod
    def _build_plain(listings: list[Listing]) -> str:
        lines = [f"SUUMO 新着物件通知 ({len(listings)}件)", "=" * 40]
        lines += [l.url for l in listings]
        return "\n".join(lines)


class Notifier:
    """LINE・Slack・メールをラップする複合通知クラス。設定されたチャンネル全てに送信する。"""

    def __init__(
        self,
        line: Optional[LineNotifier] = None,
        slack: Optional[SlackNotifier] = None,
        email: Optional[EmailNotifier] = None,
    ):
        self.line = line
        self.slack = slack
        self.email = email

        if not any([self.line, self.slack, self.email]):
            raise ValueError(
                "通知手段が設定されていません。"
                "LINE・Slack・メールのいずれかを設定してください。"
            )

    def notify(self, listings: list[Listing]) -> None:
        if not listings:
            logger.info("新着物件なし。通知をスキップします。")
            return

        if self.line:
            self.line.send_new_listings(listings)
        if self.slack:
            self.slack.send_new_listings(listings)
        if self.email:
            self.email.send_new_listings(listings)

    def notify_error(self, message: str) -> None:
        """エラー発生時に通知する。"""
        error_text = f"[SUUMO Monitor エラー]\n{message}"
        if self.line:
            self.line.send(error_text)
        if self.slack:
            self.slack.send(f":warning: {error_text}")
