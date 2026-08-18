import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tracker import (
    ListItem,
    load_history,
    prune_history,
    record_prices,
    write_items_snapshot,
)


def make_item(asin="B08XYZ1234", price=24.99, title="Water Bottle"):
    return ListItem(
        asin=asin,
        item_id=f"I_{asin}",
        title=title,
        price=price,
        currency="USD" if price is not None else None,
        available=price is not None,
        url=f"https://www.amazon.com/dp/{asin}/",
    )


def test_record_prices_creates_history(tmp_path):
    history_file = tmp_path / "prices.csv"
    record_prices(history_file, [make_item()], on_date=date(2026, 8, 18))

    rows = load_history(history_file)
    assert len(rows) == 1
    row = rows[0]
    assert row["date"] == "2026-08-18"
    assert row["key"] == "B08XYZ1234"
    assert row["price"] == "24.99"
    assert row["available"] == "true"


def test_record_prices_same_day_replaces_instead_of_duplicating(tmp_path):
    history_file = tmp_path / "prices.csv"
    day = date(2026, 8, 18)
    record_prices(history_file, [make_item(price=24.99)], on_date=day)
    record_prices(history_file, [make_item(price=19.99)], on_date=day)

    rows = load_history(history_file)
    assert len(rows) == 1
    assert rows[0]["price"] == "19.99"


def test_record_prices_accumulates_across_days(tmp_path):
    history_file = tmp_path / "prices.csv"
    record_prices(history_file, [make_item(price=24.99)], on_date=date(2026, 8, 17))
    record_prices(history_file, [make_item(price=21.50)], on_date=date(2026, 8, 18))

    rows = load_history(history_file)
    assert [(r["date"], r["price"]) for r in rows] == [
        ("2026-08-17", "24.99"),
        ("2026-08-18", "21.50"),
    ]


def test_unavailable_item_recorded_with_empty_price(tmp_path):
    history_file = tmp_path / "prices.csv"
    record_prices(history_file, [make_item(price=None)], on_date=date(2026, 8, 18))

    row = load_history(history_file)[0]
    assert row["price"] == ""
    assert row["available"] == "false"


def test_prune_history_respects_retention_window(tmp_path):
    history_file = tmp_path / "prices.csv"
    record_prices(history_file, [make_item(price=30.00)], on_date=date(2026, 5, 1))
    record_prices(history_file, [make_item(price=25.00)], on_date=date(2026, 8, 1))
    record_prices(history_file, [make_item(price=24.99)], on_date=date(2026, 8, 18))

    removed = prune_history(history_file, retention_days=30, today=date(2026, 8, 18))

    assert removed == 1
    assert [r["date"] for r in load_history(history_file)] == ["2026-08-01", "2026-08-18"]


def test_prune_history_keeps_everything_when_retention_is_none(tmp_path):
    history_file = tmp_path / "prices.csv"
    record_prices(history_file, [make_item()], on_date=date(2020, 1, 1))

    removed = prune_history(history_file, retention_days=None, today=date(2026, 8, 18))

    assert removed == 0
    assert len(load_history(history_file)) == 1


def test_write_items_snapshot(tmp_path):
    items_file = tmp_path / "items.json"
    write_items_snapshot(items_file, [make_item()])

    snapshot = json.loads(items_file.read_text())
    assert snapshot == [
        {
            "key": "B08XYZ1234",
            "asin": "B08XYZ1234",
            "item_id": "I_B08XYZ1234",
            "title": "Water Bottle",
            "price": 24.99,
            "currency": "USD",
            "available": True,
            "url": "https://www.amazon.com/dp/B08XYZ1234/",
        }
    ]
