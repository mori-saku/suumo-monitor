"""
config.py - .envから設定を読み込み、Configデータクラスに格納する。
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    search_url: str
    db_path: Path

    request_delay: float
    max_pages: int
    log_level: str

    # LINE Messaging API
    line_channel_access_token: Optional[str]
    line_user_id: Optional[str]

    # Slack Incoming Webhook
    slack_webhook_url: Optional[str]

    # メール (SMTP)
    email_enabled: bool
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    email_from: str
    email_to: list[str]

    @classmethod
    def from_env(cls) -> "Config":
        search_url = os.environ.get("SUUMO_SEARCH_URL", "").strip()
        if not search_url:
            raise ValueError(
                "SUUMO_SEARCH_URL が設定されていません。.env ファイルを確認してください。"
            )

        email_to_raw = os.environ.get("EMAIL_TO", "")
        email_to = [e.strip() for e in email_to_raw.split(",") if e.strip()]

        return cls(
            search_url=search_url,
            db_path=Path(os.environ.get("DB_PATH", "data/suumo.db")),
            request_delay=float(os.environ.get("REQUEST_DELAY_SECONDS", "3.0")),
            max_pages=int(os.environ.get("MAX_PAGES", "10")),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            line_channel_access_token=os.environ.get("LINE_CHANNEL_ACCESS_TOKEN") or None,
            line_user_id=os.environ.get("LINE_USER_ID") or None,
            slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL") or None,
            email_enabled=os.environ.get("EMAIL_ENABLED", "false").lower() == "true",
            smtp_host=os.environ.get("SMTP_HOST", "smtp.gmail.com"),
            smtp_port=int(os.environ.get("SMTP_PORT", "587")),
            smtp_username=os.environ.get("SMTP_USERNAME", ""),
            smtp_password=os.environ.get("SMTP_PASSWORD", ""),
            email_from=os.environ.get("EMAIL_FROM", ""),
            email_to=email_to,
        )

    def validate(self) -> None:
        line_ok = self.line_channel_access_token and self.line_user_id
        slack_ok = self.slack_webhook_url
        email_ok = self.email_enabled

        if not any([line_ok, slack_ok, email_ok]):
            raise ValueError(
                "通知手段が設定されていません。\n"
                "以下のいずれかを設定してください:\n"
                "  LINE: LINE_CHANNEL_ACCESS_TOKEN + LINE_USER_ID\n"
                "  Slack: SLACK_WEBHOOK_URL\n"
                "  メール: EMAIL_ENABLED=true + SMTP設定"
            )

        if self.line_channel_access_token and not self.line_user_id:
            raise ValueError("LINE_CHANNEL_ACCESS_TOKEN が設定されていますが LINE_USER_ID が未設定です。")

        if self.email_enabled:
            missing = [
                k
                for k, v in {
                    "SMTP_USERNAME": self.smtp_username,
                    "SMTP_PASSWORD": self.smtp_password,
                    "EMAIL_FROM": self.email_from,
                    "EMAIL_TO": self.email_to,
                }.items()
                if not v
            ]
            if missing:
                raise ValueError(
                    f"EMAIL_ENABLED=true ですが、以下の設定が不足しています: {missing}"
                )
