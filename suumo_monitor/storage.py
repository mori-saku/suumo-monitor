"""
storage.py - SQLiteを使って既知の物件IDを永続化し、新着を検出する。

スキーマ:
  listings(
    listing_id    TEXT PRIMARY KEY,
    url           TEXT NOT NULL,
    building_name TEXT,
    address       TEXT,
    station_access TEXT,
    rent          TEXT,
    layout        TEXT,
    area          TEXT,
    age_floors    TEXT,
    first_seen_at TEXT NOT NULL,   -- ISO 8601 UTC
    notified_at   TEXT             -- NULL: 未通知, 値あり: 通知済み
  )
  run_log(
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at         TEXT NOT NULL,
    listings_found INTEGER,
    new_listings   INTEGER,
    error          TEXT
  )
"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional, Union

from .scraper import Listing

logger = logging.getLogger(__name__)


def _physical_key(listing: "Listing") -> str:
    """住所・階数・間取り・面積から物理的な部屋の同一性を判定するキーを生成する。"""
    return f"{listing.address}|{listing.unit_floor}|{listing.layout}|{listing.area}"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    listing_id     TEXT PRIMARY KEY,
    url            TEXT NOT NULL,
    building_name  TEXT,
    address        TEXT,
    station_access TEXT,
    rent           TEXT,
    layout         TEXT,
    area           TEXT,
    age_floors     TEXT,
    unit_floor     TEXT NOT NULL DEFAULT '',
    physical_key   TEXT NOT NULL DEFAULT '',
    first_seen_at  TEXT NOT NULL,
    notified_at    TEXT
);

CREATE TABLE IF NOT EXISTS run_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at         TEXT NOT NULL,
    listings_found INTEGER,
    new_listings   INTEGER,
    error          TEXT
);
"""


class ListingStorage:
    """SUUMOの物件情報を管理するSQLiteストレージ。"""

    def __init__(self, db_path: Union[str, Path]):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
            # 既存DBへのマイグレーション: カラムが無ければ追加
            for col, definition in [
                ("unit_floor", "TEXT NOT NULL DEFAULT ''"),
                ("physical_key", "TEXT NOT NULL DEFAULT ''"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE listings ADD COLUMN {col} {definition}")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass  # すでに存在する

    def filter_new_listings(self, listings: list[Listing]) -> list[Listing]:
        """
        スクレイプ結果からDBに未登録の物件のみを返す。
        listing_id と physical_key (住所+階数+間取り+面積) の両方で重複を排除する。
        異なる仲介会社が同じ部屋を掲載している場合も弾く。
        """
        if not listings:
            return []

        ids = [l.listing_id for l in listings]
        id_placeholders = ",".join("?" * len(ids))

        # 階数が取れた物件のみphysical_keyで重複チェック
        pkeys = [_physical_key(l) for l in listings if l.unit_floor]
        pk_placeholders = ",".join("?" * len(pkeys)) if pkeys else None

        with self._conn() as conn:
            known_ids = {
                row["listing_id"]
                for row in conn.execute(
                    f"SELECT listing_id FROM listings WHERE listing_id IN ({id_placeholders})",
                    ids,
                ).fetchall()
            }
            known_pkeys: set[str] = set()
            if pk_placeholders:
                known_pkeys = {
                    row["physical_key"]
                    for row in conn.execute(
                        f"SELECT physical_key FROM listings WHERE physical_key IN ({pk_placeholders})",
                        pkeys,
                    ).fetchall()
                    if row["physical_key"]
                }

        def is_new(l: Listing) -> bool:
            if l.listing_id in known_ids:
                return False
            if l.unit_floor and _physical_key(l) in known_pkeys:
                return False
            return True

        # DB重複 + バッチ内重複（同じ部屋を複数業者が掲載）を両方排除
        seen_pkeys: set[str] = set()
        result = []
        for l in listings:
            if not is_new(l):
                continue
            if l.unit_floor:
                pk = _physical_key(l)
                if pk in seen_pkeys:
                    continue  # 同一バッチ内の重複
                seen_pkeys.add(pk)
            result.append(l)
        return result

    def delete_expired_listings(self, current_listing_ids: list[str]) -> int:
        """
        現在の検索結果に含まれないDBレコードを削除する。
        掲載終了した物件を削除することで、再掲載時に再通知できる。
        戻り値: 削除件数
        """
        if not current_listing_ids:
            return 0
        placeholders = ",".join("?" * len(current_listing_ids))
        with self._conn() as conn:
            cur = conn.execute(
                f"DELETE FROM listings WHERE listing_id NOT IN ({placeholders})",
                current_listing_ids,
            )
        return cur.rowcount

    def save_listings(self, listings: list[Listing]) -> None:
        """新着物件をDBに保存する。INSERT OR IGNOREで冪等性を保証。"""
        if not listings:
            return

        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                l.listing_id,
                l.url,
                l.building_name,
                l.address,
                l.station_access,
                l.rent,
                l.layout,
                l.area,
                l.age_floors,
                l.unit_floor,
                _physical_key(l),
                now,
                None,
            )
            for l in listings
        ]

        with self._conn() as conn:
            conn.executemany(
                """INSERT OR IGNORE INTO listings
                   (listing_id, url, building_name, address, station_access,
                    rent, layout, area, age_floors, unit_floor, physical_key,
                    first_seen_at, notified_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
        logger.info(f"{len(listings)} 件の物件をDBに保存しました。")

    def mark_notified(self, listing_ids: list[str]) -> None:
        """通知済みの物件に通知日時を記録する。"""
        if not listing_ids:
            return
        now = datetime.now(timezone.utc).isoformat()
        placeholders = ",".join("?" * len(listing_ids))
        with self._conn() as conn:
            conn.execute(
                f"UPDATE listings SET notified_at=? WHERE listing_id IN ({placeholders})",
                [now, *listing_ids],
            )

    def log_run(
        self,
        listings_found: int,
        new_listings: int,
        error: Optional[str] = None,
    ) -> None:
        """実行ログをDBに記録する。"""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO run_log (run_at, listings_found, new_listings, error) VALUES (?,?,?,?)",
                (now, listings_found, new_listings, error),
            )
