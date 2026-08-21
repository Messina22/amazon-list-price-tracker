from pathlib import Path

import pytest

from price_tracker.scraper import (
    ScrapeError,
    is_captcha_page,
    parse_list_page,
    parse_price_text,
    scrape_list,
)

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://www.amazon.com/hz/wishlist/ls/1ABCD23EFGH45"
CAPTCHA_HTML = """
<html><body>
  <form method="get" action="/errors/validateCaptcha">
    <input name="amzn" value="x"/>
  </form>
  <p>Enter the characters you see below</p>
</body></html>
"""
PAGINATION_TOKEN_HTML = """
<html><body>
<ul id="g-items">
  <li data-itemid="I1AAAAAAAAAAAA" data-price="24.99">
    <a id="itemName_I1AAAAAAAAAAAA" title="Water Bottle" href="/dp/B08XYZ1234/">Water Bottle</a>
    <span id="itemPrice_I1AAAAAAAAAAAA"><span class="a-offscreen">$24.99</span></span>
  </li>
</ul>
<input class="showMoreUrl" name="showMoreUrl"
       value="/hz/wishlist/slv/items?paginationToken=TOKEN&amp;lid=1ABCD23EFGH45"/>
<a class="wl-see-more" href="/hz/wishlist/slv/items?paginationToken=TOKEN&amp;lid=1ABCD23EFGH45">See more</a>
</body></html>
"""


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, handler):
        self.headers = {}
        self.calls = []
        self._handler = handler

    def get(self, url, timeout=30, **kwargs):
        self.calls.append(url)
        return self._handler(url, len(self.calls))


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


def test_parse_list_page_follows_pagination_token():
    items, next_url = parse_list_page(PAGINATION_TOKEN_HTML, BASE_URL)
    assert len(items) == 1
    assert items[0].asin == "B08XYZ1234"
    assert next_url is not None and "paginationToken=TOKEN" in next_url


def test_captcha_page_is_detected():
    assert is_captcha_page(CAPTCHA_HTML)
    assert not is_captcha_page((FIXTURES / "wishlist_page.html").read_text())


def test_parse_list_page_rejects_captcha():
    with pytest.raises(ScrapeError, match="CAPTCHA"):
        parse_list_page(CAPTCHA_HTML, BASE_URL)


def test_scrape_list_rejects_non_amazon_urls():
    with pytest.raises(ScrapeError):
        scrape_list("https://example.com/some/list")


def test_scrape_list_follows_pagination(monkeypatch):
    monkeypatch.setattr("price_tracker.scraper.time.sleep", lambda seconds: None)

    def handler(url, _call):
        if "lek=" in url:
            return FakeResponse((FIXTURES / "wishlist_last_page.html").read_text())
        return FakeResponse((FIXTURES / "wishlist_page.html").read_text())

    session = FakeSession(handler)
    items = scrape_list(BASE_URL, session=session)

    assert len(session.calls) == 2
    assert [item.asin for item in items] == [
        "B08XYZ1234",
        "B07ABC9876",
        "B01OLD0001",
        "B09CABLE11",
    ]


def test_scrape_list_retries_captcha_then_succeeds(monkeypatch):
    monkeypatch.setattr("price_tracker.scraper.time.sleep", lambda seconds: None)

    def handler(_url, call):
        if call == 1:
            return FakeResponse(CAPTCHA_HTML)
        return FakeResponse((FIXTURES / "wishlist_last_page.html").read_text())

    session = FakeSession(handler)
    items = scrape_list(BASE_URL, session=session)
    assert session.calls == [BASE_URL, BASE_URL]
    assert [item.asin for item in items] == ["B09CABLE11"]


def test_scrape_list_retries_empty_first_page(monkeypatch):
    monkeypatch.setattr("price_tracker.scraper.time.sleep", lambda seconds: None)

    def handler(_url, call):
        if call == 1:
            return FakeResponse("<html><ul id='g-items'></ul></html>")
        return FakeResponse((FIXTURES / "wishlist_last_page.html").read_text())

    session = FakeSession(handler)
    items = scrape_list(BASE_URL, session=session)
    assert len(session.calls) == 2
    assert items[0].asin == "B09CABLE11"


def test_scrape_list_retries_rate_limit(monkeypatch):
    monkeypatch.setattr("price_tracker.scraper.time.sleep", lambda seconds: None)

    def handler(_url, call):
        if call == 1:
            return FakeResponse("throttled", status_code=503)
        return FakeResponse((FIXTURES / "wishlist_last_page.html").read_text())

    session = FakeSession(handler)
    items = scrape_list(BASE_URL, session=session)
    assert len(session.calls) == 2
    assert items[0].asin == "B09CABLE11"


def test_scrape_list_gives_up_after_captcha_retries(monkeypatch):
    monkeypatch.setattr("price_tracker.scraper.time.sleep", lambda seconds: None)
    session = FakeSession(lambda _url, _call: FakeResponse(CAPTCHA_HTML))
    with pytest.raises(ScrapeError, match="CAPTCHA"):
        scrape_list(BASE_URL, session=session)
    assert len(session.calls) == 4
