"""Fetch and parse a public Amazon list (wishlist) page.

Amazon does not offer an API for wishlists, so this works by fetching the
public share page and parsing the HTML. Amazon changes its markup from time
to time and rate-limits aggressive clients, so the parsing here is defensive:
every selector has a fallback and a missing price is recorded as unavailable
rather than crashing the run.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# A realistic desktop browser profile. Amazon serves bot-detection pages to
# clients with no / default python-requests headers.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

MAX_PAGES = 50  # safety cap on pagination
PAGE_DELAY_SECONDS = 2.0  # politeness delay between page fetches

_ASIN_RE = re.compile(r"/dp/([A-Z0-9]{10})")
_PRICE_RE = re.compile(r"(\d[\d,]*)(?:[.,](\d{2}))?")

_CURRENCY_SYMBOLS = {
    "$": "USD",
    "£": "GBP",
    "€": "EUR",
    "¥": "JPY",
    "₹": "INR",
    "C$": "CAD",
}


class ScrapeError(Exception):
    pass


@dataclass
class ListItem:
    asin: str | None
    item_id: str | None
    title: str
    price: float | None
    currency: str | None
    available: bool
    url: str | None


def parse_price_text(text: str) -> tuple[float | None, str | None]:
    """Parse a display price like '$1,234.56' into (1234.56, 'USD')."""
    text = text.strip()
    if not text:
        return None, None

    currency = None
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if symbol in text:
            currency = code
            break
    if currency is None and (m := re.search(r"\b(USD|GBP|EUR|CAD|JPY|INR)\b", text)):
        currency = m.group(1)

    m = _PRICE_RE.search(text)
    if not m:
        return None, currency
    whole = m.group(1).replace(",", "")
    cents = m.group(2)
    value = float(f"{whole}.{cents}" if cents else whole)
    return value, currency


def _parse_item(li, base_url: str) -> ListItem:
    item_id = li.get("data-itemid")

    title = ""
    url = None
    asin = None
    name_link = li.select_one('a[id^="itemName_"]')
    if name_link:
        title = name_link.get("title") or name_link.get_text(strip=True)
        href = name_link.get("href")
        if href:
            url = urljoin(base_url, href.split("?")[0])
    if not title:
        heading = li.select_one("h2, h3")
        if heading:
            title = heading.get_text(strip=True)

    for candidate in (url or "", li.get("data-reposition-action-params") or ""):
        if m := _ASIN_RE.search(candidate):
            asin = m.group(1)
            break

    # Preferred source: the li's data-price attribute (a plain float set by
    # Amazon; "-Infinity" or missing means no offer / unavailable).
    price: float | None = None
    currency: str | None = None
    data_price = li.get("data-price")
    if data_price:
        try:
            value = float(data_price)
            if value > 0:
                price = value
        except ValueError:
            pass

    # Display price (also our only source for the currency).
    price_el = li.select_one('[id^="itemPrice_"] .a-offscreen') or li.select_one(
        '[id^="itemPrice_"]'
    )
    if price_el:
        display_price, currency = parse_price_text(price_el.get_text())
        if price is None:
            price = display_price

    return ListItem(
        asin=asin,
        item_id=item_id,
        title=title,
        price=price,
        currency=currency,
        available=price is not None,
        url=url,
    )


def parse_list_page(html: str, base_url: str) -> tuple[list[ListItem], str | None]:
    """Parse one page of a list.

    Returns the items found and the URL of the next page (None when this is
    the last page).
    """
    soup = BeautifulSoup(html, "html.parser")

    if soup.select_one("form[action*='validateCaptcha']"):
        raise ScrapeError(
            "Amazon returned a CAPTCHA page instead of the list. "
            "Try again later or from a different network."
        )

    container = soup.select_one("ul#g-items") or soup
    items = [_parse_item(li, base_url) for li in container.select("li[data-itemid]")]

    next_url = None
    if not soup.select_one("#endOfListMarker"):
        # The "show more" pagination link carries a lastEvaluatedKey (lek).
        more = soup.select_one("a.wl-see-more[href]") or soup.select_one(
            'a[href*="lek="]'
        )
        if more:
            next_url = urljoin(base_url, more["href"])

    return items, next_url


def scrape_list(list_url: str, session: requests.Session | None = None) -> list[ListItem]:
    """Fetch every page of a public Amazon list and return all its items."""
    parsed = urlparse(list_url)
    if parsed.scheme not in ("http", "https") or "amazon" not in parsed.netloc:
        raise ScrapeError(f"That does not look like an Amazon list URL: {list_url}")

    session = session or requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    all_items: list[ListItem] = []
    seen_ids: set[str] = set()
    url: str | None = list_url

    for page in range(MAX_PAGES):
        if url is None:
            break
        if page > 0:
            time.sleep(PAGE_DELAY_SECONDS)

        response = session.get(url, timeout=30)
        if response.status_code in (503, 429):
            raise ScrapeError(
                f"Amazon rate-limited the request (HTTP {response.status_code}). "
                "Try again later."
            )
        response.raise_for_status()

        items, next_url = parse_list_page(response.text, url)
        new_items = [
            item
            for item in items
            if not item.item_id or item.item_id not in seen_ids
        ]
        if not new_items:
            break
        for item in new_items:
            if item.item_id:
                seen_ids.add(item.item_id)
        all_items.extend(new_items)
        url = next_url

    if not all_items:
        raise ScrapeError(
            "No items found on the list page. Make sure the list is public and "
            "the URL is its share link (it should contain /hz/wishlist/ls/)."
        )
    return all_items
