"""
monitor.py - メインオーケストレーター。スクレイプ → 差分検出 → 通知 → 保存を実行する。

使い方:
    python -m suumo_monitor.monitor             # 通常実行
    python -m suumo_monitor.monitor --dry-run   # 通知なしで動作確認
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

from .config import Config
from .notifier import EmailNotifier, LineNotifier, Notifier, SlackNotifier
from .scraper import SuumoScraper
from .storage import ListingStorage


def setup_logging(level: str, log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(str(log_file), encoding="utf-8"),
        ],
    )


def build_notifier(cfg: Config) -> Notifier:
    line = None
    if cfg.line_channel_access_token and cfg.line_user_id:
        line = LineNotifier(cfg.line_channel_access_token, cfg.line_user_id)

    slack = SlackNotifier(cfg.slack_webhook_url) if cfg.slack_webhook_url else None

    email = None
    if cfg.email_enabled:
        email = EmailNotifier(
            smtp_host=cfg.smtp_host,
            smtp_port=cfg.smtp_port,
            username=cfg.smtp_username,
            password=cfg.smtp_password,
            from_addr=cfg.email_from,
            to_addrs=cfg.email_to,
        )
    return Notifier(line=line, slack=slack, email=email)


def run(dry_run: bool = False, search_url: Optional[str] = None) -> None:
    cfg = Config.from_env()
    if search_url:
        cfg.search_url = search_url
    cfg.validate()

    log_file = cfg.db_path.parent / "monitor.log"
    setup_logging(cfg.log_level, log_file)
    logger = logging.getLogger(__name__)

    logger.info("=" * 50)
    logger.info("SUUMO Monitor 開始")
    logger.info(f"検索URL: {cfg.search_url}")
    if dry_run:
        logger.info("[DRY RUN モード] 通知は送信されません。")

    scraper = SuumoScraper(
        request_delay=cfg.request_delay,
        max_pages=cfg.max_pages,
    )
    storage = ListingStorage(cfg.db_path)
    notifier = build_notifier(cfg)

    listings_found = 0
    new_count = 0
    error_msg = None

    try:
        # 1. 全ページをスクレイプ
        all_listings = scraper.fetch_all_listings(cfg.search_url)
        listings_found = len(all_listings)
        logger.info(f"スクレイプ完了: 合計 {listings_found} 件")

        if listings_found == 0:
            logger.warning(
                "物件が1件も取得できませんでした。"
                "SUUMOのHTML構造変更またはIPブロックの可能性があります。"
            )

        # 2. 掲載終了した物件をDBから削除
        current_ids = [l.listing_id for l in all_listings]
        expired_count = storage.delete_expired_listings(current_ids)
        if expired_count:
            logger.info(f"掲載終了: {expired_count} 件をDBから削除しました。")

        # 3. 新着を差分検出
        new_listings = storage.filter_new_listings(all_listings)
        new_count = len(new_listings)
        logger.info(f"新着: {new_count} 件")

        if new_listings:
            if not dry_run:
                # 3. 通知送信
                notifier.notify(new_listings)
                # 4. DBに保存して通知済みマーク
                storage.save_listings(new_listings)
                storage.mark_notified([l.listing_id for l in new_listings])
            else:
                logger.info("[DRY RUN] 検出された新着物件:")
                for l in new_listings:
                    logger.info(f"  {l.listing_id}: {l.building_name} {l.rent} {l.layout}")
                # ドライランでもDBには保存して重複通知を防ぐ
                storage.save_listings(new_listings)
        else:
            logger.info("新着物件なし。")

    except Exception as e:
        error_msg = str(e)
        logger.exception(f"実行中にエラーが発生しました: {e}")
        if not dry_run:
            try:
                notifier.notify_error(error_msg)
            except Exception:
                pass
    finally:
        storage.log_run(listings_found, new_count, error=error_msg)
        logger.info("SUUMO Monitor 終了")
        logger.info("=" * 50)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SUUMOの新着物件をLINE/Slack/メールで通知するモニター"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="通知を送らずに動作確認を行う (DBへの保存は行う)",
    )
    parser.add_argument(
        "--url",
        metavar="URL",
        help="検索URL (.envのSUUMO_SEARCH_URLを上書き)",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run, search_url=args.url)


if __name__ == "__main__":
    main()
