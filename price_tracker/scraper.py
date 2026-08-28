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

# Used only when curl_cffi is unavailable. Prefer impersonation headers from
# curl_cffi — a mismatched User-Agent / TLS fingerprint is what gets GitHub
# Actions (and other datacenter IPs) a CAPTCHA instead of the list.
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
FETCH_ATTEMPTS = 4
RETRY_BACKOFF_SECONDS = (3.0, 8.0, 15.0)
RETRY_HTTP_STATUSES = {429, 503}

_ASIN_RE = re.compile(r"/dp/([A-Z0-9]{10})")
_PRICE_RE = re.compile(r"(\d[\d,]*)(?:[.,](\d{2}))?")
_CAPTCHA_HINTS = (
    "validatecaptcha",
    "sorry, we just need to make sure you're not a robot",
    "enter the characters you see below",
    "opfcaptcha",
)

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


def is_captcha_page(html: str) -> bool:
    """True when Amazon served a bot-check instead of the list."""
    lowered = html.lower()
    if any(hint in lowered for hint in _CAPTCHA_HINTS):
        return True
    soup = BeautifulSoup(html, "html.parser")
    return soup.select_one("form[action*='validateCaptcha']") is not None


def new_browser_session():
    """HTTP session that looks like a real browser.

    Amazon's WAF fingerprints the TLS handshake. ``python-requests`` is easy
    to spot, so we impersonate Chrome via curl_cffi when it is installed.
    """
    try:
        from curl_cffi import requests as creq

        return creq.Session(impersonate="chrome")
    except Exception:
        session = requests.Session()
        session.headers.update(DEFAULT_HEADERS)
        return session


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


def _next_page_url(soup: BeautifulSoup, base_url: str) -> str | None:
    if soup.select_one("#endOfListMarker"):
        return None

    more = (
        soup.select_one("a.wl-see-more[href]")
        or soup.select_one('a[href*="lek="]')
        or soup.select_one('a[href*="paginationToken="]')
    )
    if more and more.get("href"):
        return urljoin(base_url, more["href"])

    show_more = soup.select_one("input.showMoreUrl[value]")
    if show_more and show_more.get("value"):
        return urljoin(base_url, show_more["value"])
    return None


def parse_list_page(html: str, base_url: str) -> tuple[list[ListItem], str | None]:
    """Parse one page of a list.

    Returns the items found and the URL of the next page (None when this is
    the last page).
    """
    if is_captcha_page(html):
        raise ScrapeError(
            "Amazon returned a CAPTCHA page instead of the list. "
            "Try again later or from a different network."
        )

    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("ul#g-items") or soup
    items = [_parse_item(li, base_url) for li in container.select("li[data-itemid]")]
    return items, _next_page_url(soup, base_url)


def _http_get(session, url: str):
    return session.get(url, timeout=30)


def _load_list_page(
    session, url: str, *, retry_empty: bool
) -> tuple[list[ListItem], str | None]:
    """GET and parse one list page, retrying CAPTCHAs and empty first loads."""
    last_error: Exception | None = None
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        if attempt > 1:
            delay = RETRY_BACKOFF_SECONDS[min(attempt - 2, len(RETRY_BACKOFF_SECONDS) - 1)]
            print(
                f"Retrying list fetch in {delay:.0f}s "
                f"(attempt {attempt}/{FETCH_ATTEMPTS}): {last_error}",
                flush=True,
            )
            time.sleep(delay)

        try:
            response = _http_get(session, url)
        except Exception as exc:
            last_error = ScrapeError(f"Failed to fetch the list: {exc}")
            continue

        status = getattr(response, "status_code", 0)
        if status in RETRY_HTTP_STATUSES:
            last_error = ScrapeError(
                f"Amazon rate-limited the request (HTTP {status}). Try again later."
            )
            continue
        try:
            response.raise_for_status()
        except Exception as exc:
            last_error = ScrapeError(f"Amazon returned HTTP {status or 'error'}: {exc}")
            continue

        html = response.text
        if is_captcha_page(html):
            last_error = ScrapeError(
                "Amazon returned a CAPTCHA page instead of the list. "
                "Try again later or from a different network."
            )
            continue

        try:
            items, next_url = parse_list_page(html, url)
        except ScrapeError as exc:
            last_error = exc
            continue
        if items or not retry_empty:
            return items, next_url
        last_error = ScrapeError(
            "No items found on the list page. Make sure the list is public and "
            "the URL is its share link (it should contain /hz/wishlist/ls/)."
        )

    raise last_error or ScrapeError("Failed to fetch the list.")


def scrape_list(list_url: str, session=None) -> list[ListItem]:
    """Fetch every page of a public Amazon list and return all its items."""
    parsed = urlparse(list_url)
    if parsed.scheme not in ("http", "https") or "amazon" not in parsed.netloc:
        raise ScrapeError(f"That does not look like an Amazon list URL: {list_url}")

    session = session or new_browser_session()

    all_items: list[ListItem] = []
    seen_ids: set[str] = set()
    url: str | None = list_url

    for page in range(MAX_PAGES):
        if url is None:
            break
        if page > 0:
            time.sleep(PAGE_DELAY_SECONDS)

        items, next_url = _load_list_page(session, url, retry_empty=(page == 0))
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
