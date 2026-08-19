import json
from datetime import date

import pytest

from price_tracker.dashboard import (
    build_dashboard_data,
    build_dashboard_site,
    write_dashboard_json,
)
from price_tracker.storage import PriceRecord, record_prices, write_items_snapshot
from tests.test_storage import make_item


def test_build_dashboard_data_aggregates_history_per_item():
    history = [
        PriceRecord(
            date="2026-08-16",
            key="B08XYZ1234",
            asin="B08XYZ1234",
            item_id="I_B08XYZ1234",
            title="Water Bottle",
            price="24.99",
            currency="USD",
            available="true",
        ),
        PriceRecord(
            date="2026-08-17",
            key="B08XYZ1234",
            asin="B08XYZ1234",
            item_id="I_B08XYZ1234",
            title="Water Bottle",
            price="",
            currency="",
            available="false",
        ),
        PriceRecord(
            date="2026-08-18",
            key="B08XYZ1234",
            asin="B08XYZ1234",
            item_id="I_B08XYZ1234",
            title="Water Bottle",
            price="19.99",
            currency="USD",
            available="true",
        ),
        PriceRecord(
            date="2026-08-18",
            key="B00OTHER01",
            asin="B00OTHER01",
            item_id="I_B00OTHER01",
            title="Towel",
            price="10.00",
            currency="USD",
            available="true",
        ),
    ]
    snapshot = [
        {
            "key": "B08XYZ1234",
            "asin": "B08XYZ1234",
            "title": "Water Bottle",
            "price": 19.99,
            "currency": "USD",
            "available": True,
            "url": "https://www.amazon.com/dp/B08XYZ1234/",
        }
    ]

    payload = build_dashboard_data(history, snapshot, list_url="https://example.test/list")

    assert payload["listUrl"] == "https://example.test/list"
    assert payload["lastDate"] == "2026-08-18"
    assert payload["itemCount"] == 2
    bottle = payload["items"][0]
    assert bottle["key"] == "B08XYZ1234"
    assert bottle["url"] == "https://www.amazon.com/dp/B08XYZ1234/"
    assert bottle["currentPrice"] == 19.99
    assert bottle["firstPrice"] == 24.99
    assert bottle["minPrice"] == 19.99
    assert bottle["maxPrice"] == 24.99
    assert bottle["change"] == -5.0
    assert bottle["changePct"] == pytest.approx(-20.01)
    assert [point["price"] for point in bottle["history"]] == [24.99, None, 19.99]
    assert payload["items"][1]["key"] == "B00OTHER01"
    assert payload["items"][1]["title"] == "Towel"
    assert payload["items"][1]["change"] is None
    assert payload["items"][1]["changePct"] is None


def test_write_dashboard_json_and_site(tmp_path):
    history_file = tmp_path / "price_history.csv"
    items_file = tmp_path / "items.json"
    dashboard_src = tmp_path / "dashboard"
    dashboard_src.mkdir()
    for name, body in {
        "index.html": "<!doctype html><title>dash</title>",
        "styles.css": "body{}",
        "app.js": "console.log('ok')",
    }.items():
        (dashboard_src / name).write_text(body)

    record_prices(
        history_file,
        [make_item(price=24.99), make_item(asin="B00OTHER01", price=10.00, title="Towel")],
        on_date=date(2026, 8, 17),
    )
    record_prices(
        history_file,
        [make_item(price=19.99), make_item(asin="B00OTHER01", price=10.00, title="Towel")],
        on_date=date(2026, 8, 18),
    )
    write_items_snapshot(
        items_file,
        [make_item(price=19.99), make_item(asin="B00OTHER01", price=10.00, title="Towel")],
    )

    output = write_dashboard_json(
        history_file, items_file, tmp_path / "dashboard.json", list_url="https://amzn/list"
    )
    payload = json.loads(output.read_text())
    assert payload["itemCount"] == 2
    assert payload["availableCount"] == 2
    assert payload["items"][0]["history"][0]["date"] == "2026-08-17"

    out_dir = tmp_path / "site"
    build_dashboard_site(
        history_file,
        items_file,
        out_dir,
        list_url="https://amzn/list",
        dashboard_dir=dashboard_src,
    )
    assert (out_dir / "index.html").is_file()
    assert (out_dir / "styles.css").is_file()
    assert (out_dir / "app.js").is_file()
    site_payload = json.loads((out_dir / "data" / "dashboard.json").read_text())
    assert site_payload["itemCount"] == 2
    assert (out_dir / "data" / "price_history.csv").is_file()
