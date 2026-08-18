#!/usr/bin/env python3
"""Amazon list price tracker — single file, standard library only.

Fetches a public Amazon list (wishlist) share page, records each item's price
to a CSV file (one row per item per day), and prunes rows older than the
configured retention window.

Usage:
    python3 tracker.py run        # fetch the list and record today's prices
    python3 tracker.py history    # print the stored price history per item

Configuration lives in config.json next to this script.

Amazon has no wishlist API, so this parses the public page's HTML. The parsing
is defensive — every selector has a fallback, and an unavailable item is
recorded as such rather than crashing the run.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

CONFIG_PATH = Path(__file__).parent / "config.json"

# A realistic desktop browser profile. Amazon serves bot-detection pages to
# clients with default urllib headers.
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

MAX_PAGES = 50  # safety cap on pagination
PAGE_DELAY_SECONDS = 2.0  # politeness delay between page fetches

HISTORY_COLUMNS = ["date", "key", "asin", "item_id", "title", "price", "currency", "available"]

_ASIN_RE = re.compile(r"/dp/([A-Z0-9]{10})")
_PRICE_RE = re.compile(r"(\d[\d,]*)(?:[.,](\d{2}))?")
_CURRENCY_SYMBOLS = {"$": "USD", "£": "GBP", "€": "EUR", "¥": "JPY", "₹": "INR"}

# Elements that never get a closing tag; needed for depth tracking.
_VOID_TAGS = frozenset(
    "area base br col embed hr img input link meta source track wbr".split()
)


class TrackerError(Exception):
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


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@dataclass
class Config:
    list_url: str
    retention_days: int | None
    history_file: Path
    items_file: Path


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        raise TrackerError(f"Config file not found: {path}")
    raw = json.loads(path.read_text())

    list_url = (raw.get("list_url") or "").strip()
    if not list_url:
        raise TrackerError(
            "No list_url configured. Edit config.json and set list_url to the "
            "share link of your public Amazon list."
        )

    retention_days = raw.get("retention_days")
    if retention_days is not None:
        if not isinstance(retention_days, int) or retention_days <= 0:
            raise TrackerError(
                "retention_days must be a positive whole number of days, "
                "or null to keep history forever"
            )

    base = path.parent
    return Config(
        list_url=list_url,
        retention_days=retention_days,
        history_file=base / (raw.get("history_file") or "data/price_history.csv"),
        items_file=base / (raw.get("items_file") or "data/items.json"),
    )


# --------------------------------------------------------------------------
# HTML parsing (stdlib html.parser)
# --------------------------------------------------------------------------


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
    return float(f"{whole}.{cents}" if cents else whole), currency


class _ListPageParser(HTMLParser):
    """Extracts wishlist items from one page of a list.

    Tracks nesting depth manually so it knows when it leaves an item's <li>
    or a price container, since html.parser has no DOM.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self.end_of_list = False
        self.next_href: str | None = None

        self._item: dict | None = None
        self._li_depth = 0  # nested <li> depth inside the current item
        self._price_depth = 0  # >0 while inside an itemPrice_* container
        self._offscreen_depth = 0  # >0 while inside a .a-offscreen span
        self._name_link_depth = 0  # >0 while inside the itemName_* link
        self._price_text: list[str] = []
        self._title_text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        a = dict(attrs)
        el_id = a.get("id", "")

        if el_id == "endOfListMarker":
            self.end_of_list = True

        if tag == "a" and a.get("href"):
            classes = a.get("class", "")
            if "wl-see-more" in classes or "lek=" in a["href"]:
                self.next_href = a["href"]

        if tag == "li" and "data-itemid" in a:
            self._finish_item()
            self._item = {
                "item_id": a.get("data-itemid"),
                "data_price": a.get("data-price"),
                "action_params": a.get("data-reposition-action-params", ""),
                "title": "",
                "href": None,
                "price_text": "",
            }
            self._li_depth = 1
            return

        if self._item is None:
            return
        if tag == "li":
            self._li_depth += 1
        if tag in _VOID_TAGS:
            return

        if self._name_link_depth:
            self._name_link_depth += 1
        elif tag == "a" and el_id.startswith("itemName_"):
            self._item["href"] = a.get("href")
            if a.get("title"):
                self._item["title"] = a["title"].strip()
            else:
                self._name_link_depth = 1
                self._title_text = []

        if self._price_depth:
            self._price_depth += 1
            if self._offscreen_depth:
                self._offscreen_depth += 1
            elif "a-offscreen" in a.get("class", ""):
                self._offscreen_depth = 1
                self._price_text = []
        elif el_id.startswith("itemPrice_"):
            self._price_depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self._item is None or tag in _VOID_TAGS:
            return
        if self._name_link_depth:
            self._name_link_depth -= 1
            if self._name_link_depth == 0 and not self._item["title"]:
                self._item["title"] = "".join(self._title_text).strip()
        if self._price_depth:
            if self._offscreen_depth:
                self._offscreen_depth -= 1
                if self._offscreen_depth == 0 and not self._item["price_text"]:
                    self._item["price_text"] = "".join(self._price_text).strip()
            self._price_depth -= 1
            if self._price_depth == 0 and not self._item["price_text"]:
                # No .a-offscreen child: fall back to the container's own text.
                self._item["price_text"] = "".join(self._price_text).strip()
        if tag == "li":
            self._li_depth -= 1
            if self._li_depth == 0:
                self._finish_item()

    def handle_data(self, data: str) -> None:
        if self._item is None:
            return
        if self._name_link_depth:
            self._title_text.append(data)
        if self._price_depth:
            self._price_text.append(data)

    def _finish_item(self) -> None:
        if self._item is not None:
            self.items.append(self._item)
            self._item = None
            self._li_depth = 0
            self._price_depth = 0
            self._offscreen_depth = 0
            self._name_link_depth = 0

    def close(self) -> None:
        super().close()
        self._finish_item()


def parse_list_page(html: str, base_url: str) -> tuple[list[ListItem], str | None]:
    """Parse one page of a list.

    Returns the items found and the URL of the next page (None when this is
    the last page).
    """
    if "validateCaptcha" in html:
        raise TrackerError(
            "Amazon returned a CAPTCHA page instead of the list. "
            "Try again later or from a different network."
        )

    parser = _ListPageParser()
    parser.feed(html)
    parser.close()

    items = []
    for raw in parser.items:
        url = None
        if raw["href"]:
            url = urljoin(base_url, raw["href"].split("?")[0])

        asin = None
        for candidate in (url or "", raw["action_params"]):
            if m := _ASIN_RE.search(candidate):
                asin = m.group(1)
                break

        # Preferred source: the li's data-price attribute (a plain float set
        # by Amazon; "-Infinity" or missing means no offer / unavailable).
        price = None
        if raw["data_price"]:
            try:
                value = float(raw["data_price"])
                if value > 0:
                    price = value
            except ValueError:
                pass

        display_price, currency = parse_price_text(raw["price_text"])
        if price is None:
            price = display_price

        items.append(
            ListItem(
                asin=asin,
                item_id=raw["item_id"],
                title=raw["title"],
                price=price,
                currency=currency,
                available=price is not None,
                url=url,
            )
        )

    next_url = None
    if not parser.end_of_list and parser.next_href:
        next_url = urljoin(base_url, parser.next_href)
    return items, next_url


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------


def fetch_page(url: str) -> str:
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                body = gzip.GzipFile(fileobj=io.BytesIO(body)).read()
            return body.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (429, 503):
            raise TrackerError(
                f"Amazon rate-limited the request (HTTP {exc.code}). Try again later."
            ) from exc
        raise TrackerError(f"HTTP {exc.code} fetching {url}") from exc
    except urllib.error.URLError as exc:
        raise TrackerError(f"Could not fetch {url}: {exc.reason}") from exc


def scrape_list(list_url: str, fetch=fetch_page) -> list[ListItem]:
    """Fetch every page of a public Amazon list and return all its items."""
    parsed = urlparse(list_url)
    if parsed.scheme not in ("http", "https") or "amazon" not in parsed.netloc:
        raise TrackerError(f"That does not look like an Amazon list URL: {list_url}")

    all_items: list[ListItem] = []
    seen_ids: set[str] = set()
    url: str | None = list_url

    for page in range(MAX_PAGES):
        if url is None:
            break
        if page > 0:
            time.sleep(PAGE_DELAY_SECONDS)

        items, next_url = parse_list_page(fetch(url), url)
        new_items = [
            item for item in items if not item.item_id or item.item_id not in seen_ids
        ]
        if not new_items:
            break
        for item in new_items:
            if item.item_id:
                seen_ids.add(item.item_id)
        all_items.extend(new_items)
        url = next_url

    if not all_items:
        raise TrackerError(
            "No items found on the list page. Make sure the list is public and "
            "the URL is its share link (it should contain /hz/wishlist/ls/)."
        )
    return all_items


# --------------------------------------------------------------------------
# Storage (CSV history + JSON snapshot)
# --------------------------------------------------------------------------


def item_key(item: ListItem) -> str:
    return item.asin or item.item_id or item.title


def load_history(history_file: Path) -> list[dict]:
    if not history_file.exists():
        return []
    with history_file.open(newline="") as f:
        return [
            {column: row.get(column, "") for column in HISTORY_COLUMNS}
            for row in csv.DictReader(f)
        ]


def _write_history(history_file: Path, rows: list[dict]) -> None:
    history_file.parent.mkdir(parents=True, exist_ok=True)
    with history_file.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def record_prices(
    history_file: Path, items: list[ListItem], on_date: date | None = None
) -> list[dict]:
    """Append today's prices to the history file.

    Running more than once on the same day replaces that day's rows instead of
    duplicating them, so the file holds at most one row per item per day.
    """
    day = (on_date or date.today()).isoformat()
    new_rows = [
        {
            "date": day,
            "key": item_key(item),
            "asin": item.asin or "",
            "item_id": item.item_id or "",
            "title": item.title,
            "price": f"{item.price:.2f}" if item.price is not None else "",
            "currency": item.currency or "",
            "available": "true" if item.available else "false",
        }
        for item in items
    ]

    replaced = {row["key"] for row in new_rows}
    rows = [
        row
        for row in load_history(history_file)
        if not (row["date"] == day and row["key"] in replaced)
    ]
    rows.extend(new_rows)
    rows.sort(key=lambda row: (row["date"], row["key"]))
    _write_history(history_file, rows)
    return new_rows


def prune_history(
    history_file: Path, retention_days: int | None, today: date | None = None
) -> int:
    """Delete rows older than the retention window. Returns rows removed."""
    if retention_days is None:
        return 0
    cutoff = ((today or date.today()) - timedelta(days=retention_days)).isoformat()

    rows = load_history(history_file)
    kept = [row for row in rows if row["date"] >= cutoff]
    removed = len(rows) - len(kept)
    if removed:
        _write_history(history_file, kept)
    return removed


def write_items_snapshot(items_file: Path, items: list[ListItem]) -> None:
    """Save the latest view of the list (titles, URLs, current prices)."""
    items_file.parent.mkdir(parents=True, exist_ok=True)
    snapshot = [
        {
            "key": item_key(item),
            "asin": item.asin,
            "item_id": item.item_id,
            "title": item.title,
            "price": item.price,
            "currency": item.currency,
            "available": item.available,
            "url": item.url,
        }
        for item in items
    ]
    items_file.write_text(json.dumps(snapshot, indent=2) + "\n")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _format_price(row: dict) -> str:
    return f"{row['price']} {row['currency']}".strip() if row["price"] else "unavailable"


def cmd_run(config: Config) -> int:
    print(f"Fetching list: {config.list_url}")
    items = scrape_list(config.list_url)
    print(f"Found {len(items)} item(s)")

    rows = record_prices(config.history_file, items)
    write_items_snapshot(config.items_file, items)
    for row in rows:
        print(f"  {row['date']}  {_format_price(row):>16}  {row['title'][:70]}")

    removed = prune_history(config.history_file, config.retention_days)
    if removed:
        print(f"Pruned {removed} row(s) older than {config.retention_days} days")
    print(f"History written to {config.history_file}")
    return 0


def cmd_history(config: Config) -> int:
    rows = load_history(config.history_file)
    if not rows:
        print("No price history recorded yet. Run: python3 tracker.py run")
        return 0

    by_item = defaultdict(list)
    titles = {}
    for row in rows:
        by_item[row["key"]].append(row)
        titles[row["key"]] = row["title"]

    for key, item_rows in sorted(by_item.items()):
        print(f"\n{titles[key]}  [{key}]")
        for row in item_rows:
            print(f"  {row['date']}  {_format_price(row)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tracker.py", description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=CONFIG_PATH, help="path to config file"
    )
    parser.add_argument("command", choices=["run", "history"], help="what to do")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        return cmd_run(config) if args.command == "run" else cmd_history(config)
    except TrackerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
