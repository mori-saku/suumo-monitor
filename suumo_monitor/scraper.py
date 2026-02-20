"""
scraper.py - SUUMOの検索結果ページを取得し、物件情報を抽出する。
"""

import logging
import re
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://suumo.jp"

# CSSセレクター定義
# SUUMOのHTML構造が変わった場合はここを修正する
SELECTORS = {
    "listing_item": "div.cassetteitem",
    "building_name": "div.cassetteitem_content-title",
    "address": "li.cassetteitem_detail-col1",
    "station_access": "li.cassetteitem_detail-col2",
    "age_floors": "li.cassetteitem_detail-col3",
    "unit_rows": "table.cassetteitem_other tbody tr",
    "unit_link": "td.ui-text--bold a",
    "unit_rent": "span.cassetteitem_other-emphasis",
    "unit_layout": "span.cassetteitem_madori",
    "unit_area": "span.cassetteitem_menseki",
    "next_page": "div.pagination.pagination-parts a[href]",
}


@dataclass
class Listing:
    """SUUMOの1ユニット分の物件情報"""

    listing_id: str
    url: str
    building_name: str
    address: str
    station_access: str
    rent: str
    layout: str
    area: str
    age_floors: str
    unit_floor: str  # 部屋の階数 (例: "3階")


class SuumoScraper:
    """
    SUUMOの検索結果ページを取得してパースするスクレイパー。

    - サーバーサイドレンダリングのHTML取得のみ (Selenium不要)
    - ページネーションを自動追跡
    - レートリミット対策として各ページ取得間にウェイトを挿入
    """

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    def __init__(self, request_delay: float = 3.0, timeout: int = 30, max_pages: int = 10):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self.request_delay = request_delay
        self.timeout = timeout
        self.max_pages = max_pages

    def fetch_all_listings(self, search_url: str) -> list[Listing]:
        """
        検索URLから全ページの物件一覧を取得して返す。
        ページネーションを自動的に追跡する。
        """
        all_listings: list[Listing] = []
        url: Optional[str] = search_url
        page_num = 1

        while url and page_num <= self.max_pages:
            logger.info(f"ページ {page_num} を取得中: {url}")
            html = self._fetch_page(url)
            if not html:
                logger.warning(f"ページ {page_num} の取得に失敗しました。スキップします。")
                break

            soup = BeautifulSoup(html, "lxml")
            page_listings = self._parse_listings(soup)
            all_listings.extend(page_listings)
            logger.info(f"  → {len(page_listings)} 件取得 (累計: {len(all_listings)} 件)")

            url = self._get_next_page_url(soup)
            page_num += 1

            if url:
                time.sleep(self.request_delay)

        return all_listings

    def _fetch_page(self, url: str) -> Optional[str]:
        """1ページ分のHTMLを取得して返す。失敗時はNoneを返す。"""
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding
            return resp.text
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                logger.warning("レートリミット (429)。60秒待機してリトライします。")
                time.sleep(60)
                try:
                    resp = self.session.get(url, timeout=self.timeout)
                    resp.raise_for_status()
                    resp.encoding = resp.apparent_encoding
                    return resp.text
                except requests.exceptions.RequestException as e2:
                    logger.error(f"リトライも失敗: {e2}")
                    return None
            logger.error(f"HTTPエラー ({url}): {e}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"ネットワークエラー ({url}): {e}")
            return None

    def _parse_listings(self, soup: BeautifulSoup) -> list[Listing]:
        """1ページ分のHTMLから全物件情報を抽出する。"""
        listings: list[Listing] = []
        cards = soup.select(SELECTORS["listing_item"])

        if not cards:
            logger.warning(
                "物件カードが見つかりませんでした。SUUMOのHTML構造が変わった可能性があります。"
            )

        for card in cards:
            building_name = self._text(card, SELECTORS["building_name"])
            address = self._text(card, SELECTORS["address"])
            station_access = self._text(card, SELECTORS["station_access"])
            age_floors = self._text(card, SELECTORS["age_floors"])

            unit_rows = card.select(SELECTORS["unit_rows"])
            for row in unit_rows:
                link_tag = row.select_one(SELECTORS["unit_link"])
                if not link_tag or not link_tag.get("href"):
                    continue

                href = str(link_tag["href"])
                listing_id = self._extract_listing_id(href)
                if not listing_id:
                    continue

                # 行テキストから "X階" を抽出 (例: "3階", "1階")
                row_text = row.get_text(" ", strip=True)
                floor_match = re.search(r"\d+階", row_text)
                unit_floor = floor_match.group(0) if floor_match else ""

                listings.append(
                    Listing(
                        listing_id=listing_id,
                        url=urljoin(BASE_URL, href),
                        building_name=building_name,
                        address=address,
                        station_access=station_access,
                        rent=self._text(row, SELECTORS["unit_rent"]),
                        layout=self._text(row, SELECTORS["unit_layout"]),
                        area=self._text(row, SELECTORS["unit_area"]),
                        age_floors=age_floors,
                        unit_floor=unit_floor,
                    )
                )

        return listings

    def _extract_listing_id(self, href: str) -> Optional[str]:
        """
        URLからユニークなIDを抽出する。
        - /chintai/jnc_000104425407/?bc=... → "jnc_000104425407"
        - フォールバック: bcパラメータを使用
        """
        match = re.search(r"(jnc_\w+)", href)
        if match:
            return match.group(1)

        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        bc = qs.get("bc", [None])[0]
        return f"bc_{bc}" if bc else None

    def _get_next_page_url(self, soup: BeautifulSoup) -> Optional[str]:
        """「次へ」リンクのURLを取得する。なければNoneを返す。"""
        for a in soup.select(SELECTORS["next_page"]):
            text = a.get_text(strip=True)
            if "次へ" in text or text == ">":
                href = a.get("href")
                if href:
                    return urljoin(BASE_URL, str(href))
        return None

    @staticmethod
    def _text(element, selector: str) -> str:
        """セレクターで要素を取得し、テキストを返す。見つからない場合は空文字列。"""
        el = element.select_one(selector)
        return el.get_text(strip=True) if el else ""
