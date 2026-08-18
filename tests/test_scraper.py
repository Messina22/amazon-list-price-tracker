import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tracker import TrackerError, parse_list_page, parse_price_text, scrape_list

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://www.amazon.com/hz/wishlist/ls/1ABCD23EFGH45"


def test_parse_list_page_extracts_items():
    html = (FIXTURES / "wishlist_page.html").read_text()
    items, next_url = parse_list_page(html, BASE_URL)

    assert len(items) == 3

    bottle = items[0]
    assert bottle.asin == "B08XYZ1234"
    assert bottle.item_id == "I1AAAAAAAAAAAA"
    assert bottle.title == "Stainless Steel Water Bottle, 32oz"
    assert bottle.price == 24.99
    assert bottle.currency == "USD"
    assert bottle.available
    assert bottle.url == "https://www.amazon.com/dp/B08XYZ1234/"

    headphones = items[1]
    assert headphones.price == 1149.00

    unavailable = items[2]
    assert unavailable.asin == "B01OLD0001"
    assert unavailable.price is None
    assert not unavailable.available

    assert next_url is not None and "lek=NEXT_PAGE_TOKEN" in next_url


def test_parse_last_page_has_no_next_url():
    html = (FIXTURES / "wishlist_last_page.html").read_text()
    items, next_url = parse_list_page(html, BASE_URL)
    assert len(items) == 1
    assert items[0].asin == "B09CABLE11"
    assert next_url is None


def test_captcha_page_raises():
    with pytest.raises(TrackerError, match="CAPTCHA"):
        parse_list_page('<form action="/errors/validateCaptcha"></form>', BASE_URL)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$24.99", (24.99, "USD")),
        ("$1,149.00", (1149.00, "USD")),
        ("£9.50", (9.50, "GBP")),
        ("€100", (100.0, "EUR")),
        ("", (None, None)),
        ("Currently unavailable", (None, None)),
    ],
)
def test_parse_price_text(text, expected):
    assert parse_price_text(text) == expected


def test_scrape_list_rejects_non_amazon_urls():
    with pytest.raises(TrackerError):
        scrape_list("https://example.com/some/list")


def test_scrape_list_follows_pagination(monkeypatch):
    monkeypatch.setattr("tracker.time.sleep", lambda seconds: None)

    calls = []

    def fake_fetch(url):
        calls.append(url)
        if "lek=" in url:
            return (FIXTURES / "wishlist_last_page.html").read_text()
        return (FIXTURES / "wishlist_page.html").read_text()

    items = scrape_list(BASE_URL, fetch=fake_fetch)

    assert len(calls) == 2
    assert [item.asin for item in items] == [
        "B08XYZ1234",
        "B07ABC9876",
        "B01OLD0001",
        "B09CABLE11",
    ]
