from datetime import date

import pytest

from price_tracker.render import render_html, render_text, render_webhook_text, subject
from price_tracker.report import (
    baseline_days,
    build_report,
    format_change,
    format_pct,
    format_price,
    report_to_dict,
)
from price_tracker.storage import PriceRecord


def record(day, price, key="B08XYZ1234", title="Water Bottle", available=True):
    return PriceRecord(
        date=day,
        key=key,
        asin=key,
        item_id=f"I_{key}",
        title=title,
        price="" if price is None else f"{price:.2f}",
        currency="USD" if price is not None else "",
        available="true" if available else "false",
    )


def snapshot(price, key="B08XYZ1234", title="Water Bottle", available=True):
    return [
        {
            "key": key,
            "asin": key,
            "item_id": f"I_{key}",
            "title": title,
            "price": price,
            "currency": "USD",
            "available": available,
            "url": f"https://www.amazon.com/dp/{key}/",
        }
    ]


def test_baseline_days_accepts_names_and_raw_days():
    assert baseline_days("week") == 7
    assert baseline_days("month") == 30
    assert baseline_days("14") == 14
    with pytest.raises(ValueError):
        baseline_days("fortnight")
    with pytest.raises(ValueError):
        baseline_days("0")


def test_current_price_compared_against_trailing_average():
    history = [
        record("2026-08-16", 30.00),
        record("2026-08-17", 20.00),
        record("2026-08-18", 25.00),
    ]
    report = build_report(
        history, snapshot(21.00), baseline="week", as_of=date(2026, 8, 19)
    )

    item = report.items[0]
    assert item.current_price == 21.00
    assert item.baseline_average == 25.00  # (30 + 20 + 25) / 3
    assert item.baseline_samples == 3
    assert item.change == -4.00
    assert item.change_pct == -16.0
    assert item.direction == "down"
    assert item.previous_price == 25.00
    assert item.previous_change == -4.00
    assert item.window_min == 20.00
    assert item.window_max == 30.00


def test_baseline_excludes_rows_outside_the_window():
    history = [
        record("2026-08-05", 100.00),  # older than a week, inside a month
        record("2026-08-17", 10.00),
        record("2026-08-18", 20.00),
    ]
    week = build_report(history, snapshot(15.00), baseline="week", as_of=date(2026, 8, 19))
    month = build_report(history, snapshot(15.00), baseline="month", as_of=date(2026, 8, 19))

    assert week.items[0].baseline_average == 15.00
    assert week.items[0].change == 0.0
    assert month.items[0].baseline_average == 43.33
    assert month.items[0].change == pytest.approx(-28.33)


def test_todays_row_is_not_part_of_its_own_baseline():
    history = [record("2026-08-18", 40.00), record("2026-08-19", 10.00)]
    report = build_report(history, [], baseline="week", as_of=date(2026, 8, 19))

    item = report.items[0]
    assert item.current_price == 10.00
    assert item.baseline_average == 40.00
    assert item.change == -30.00


def test_all_time_low_flagged_only_when_the_price_has_moved():
    moved = build_report(
        [record("2026-08-17", 30.00), record("2026-08-18", 25.00)],
        snapshot(20.00),
        as_of=date(2026, 8, 19),
    )
    assert moved.items[0].is_all_time_low is True
    assert moved.all_time_lows == moved.items

    flat = build_report(
        [record("2026-08-17", 25.00), record("2026-08-18", 25.00)],
        snapshot(25.00),
        as_of=date(2026, 8, 19),
    )
    assert flat.items[0].is_all_time_low is False


def test_stock_changes_are_detected():
    back = build_report(
        [record("2026-08-18", None, available=False)],
        snapshot(25.00, available=True),
        as_of=date(2026, 8, 19),
    )
    assert back.items[0].stock_change == "back_in_stock"

    gone = build_report(
        [record("2026-08-18", 25.00, available=True)],
        snapshot(None, available=False),
        as_of=date(2026, 8, 19),
    )
    assert gone.items[0].stock_change == "out_of_stock"
    assert len(gone.stock_changes) == 1


def test_movers_respect_the_threshold_and_sort_by_percent():
    history = [
        record("2026-08-18", 100.00, key="AAAAAAAAAA", title="Big drop"),
        record("2026-08-18", 100.00, key="BBBBBBBBBB", title="Tiny drop"),
        record("2026-08-18", 100.00, key="CCCCCCCCCC", title="Rise"),
    ]
    snap = (
        snapshot(80.00, key="AAAAAAAAAA", title="Big drop")
        + snapshot(99.50, key="BBBBBBBBBB", title="Tiny drop")
        + snapshot(110.00, key="CCCCCCCCCC", title="Rise")
    )
    report = build_report(
        history, snap, as_of=date(2026, 8, 19), min_change_percent=1.0
    )

    assert [item.title for item in report.movers] == ["Big drop", "Rise"]
    assert [item.title for item in report.drops] == ["Big drop"]
    assert [item.title for item in report.rises] == ["Rise"]
    assert report.total_current == 289.50
    assert report.total_baseline == 300.00
    assert report.total_change == -10.50


def test_items_without_history_are_reported_without_a_comparison():
    report = build_report([], snapshot(25.00), as_of=date(2026, 8, 19))
    item = report.items[0]
    assert item.current_price == 25.00
    assert item.baseline_average is None
    assert item.change is None
    assert item.direction == "flat"
    assert report.movers == []
    assert report.has_content is True


def test_empty_history_reports_nothing_to_compare():
    report = build_report([], [], as_of=date(2026, 8, 19))
    assert report.has_content is False
    assert "No prices recorded yet" in render_text(report)
    assert "no price data yet" in subject(report)


def test_price_formatting_helpers():
    assert format_price(1234.5, "USD") == "$1,234.50"
    assert format_price(10, "SEK") == "10.00 SEK"
    assert format_price(None) == "—"
    assert format_change(-4.0, "USD") == "-$4.00"
    assert format_change(4.0, "USD") == "+$4.00"
    assert format_pct(-16.0) == "-16.0%"
    assert format_pct(None) == "—"


def test_renderings_include_the_headline_numbers():
    history = [record("2026-08-17", 30.00), record("2026-08-18", 30.00)]
    report = build_report(
        history, snapshot(21.00), baseline="week", as_of=date(2026, 8, 19),
        list_url="https://www.amazon.com/hz/wishlist/ls/TEST",
    )

    text = render_text(report)
    assert "Price drops (1)" in text
    assert "$21.00" in text
    assert "-30.0%" in text
    assert "https://www.amazon.com/hz/wishlist/ls/TEST" in text

    html = render_html(report)
    assert "<table" in html
    assert "$21.00" in html
    assert "all-time low" in html

    hook = render_webhook_text(report)
    assert "Water Bottle" in hook
    assert "-30.0%" in hook

    assert subject(report) == "Amazon list report — 1 price drop, 1 all-time low (2026-08-19)"


@pytest.mark.parametrize(
    "frequency, prefix",
    [("weekly", "Weekly "), ("daily", "Daily "), ("", "")],
)
def test_subject_names_the_send_cadence(frequency, prefix):
    report = build_report(
        [record("2026-08-18", 30.00)], snapshot(30.00), baseline="month",
        as_of=date(2026, 8, 19), frequency=frequency,
    )
    assert subject(report).startswith(f"{prefix}Amazon list report")


def test_body_names_the_comparison_window():
    week = build_report([record("2026-08-18", 30.00)], snapshot(30.00),
                        baseline="week", as_of=date(2026, 8, 19))
    month = build_report([record("2026-08-18", 30.00)], snapshot(30.00),
                         baseline="month", as_of=date(2026, 8, 19))
    custom = build_report([record("2026-08-18", 30.00)], snapshot(30.00),
                          baseline="14", as_of=date(2026, 8, 19))

    assert "past week" in render_text(week)
    assert "past month" in render_text(month)
    assert "past 14 days" in render_text(custom)


def test_unavailable_item_has_no_current_price():
    report = build_report(
        [record("2026-08-17", 25.00), record("2026-08-18", 25.00)],
        snapshot(None, available=False),
        as_of=date(2026, 8, 19),
    )
    item = report.items[0]
    # The last price we saw is not a price you can pay today.
    assert item.available is False
    assert item.current_price is None
    assert item.change is None
    assert item.previous_price == 25.00
    assert item.stock_change == "out_of_stock"
    assert item.is_all_time_low is False
    assert report.movers == []

    text = render_text(report)
    assert "Availability changes" in text
    assert "Water Bottle — out of stock" in text


def test_html_escapes_item_titles():
    history = [record("2026-08-18", 30.00, title="Bolt <b>&</b> Nut")]
    report = build_report(
        history, snapshot(30.00, title="Bolt <b>&</b> Nut"), as_of=date(2026, 8, 19)
    )
    html = render_html(report)
    assert "Bolt &lt;b&gt;&amp;&lt;/b&gt; Nut" in html
    assert "<b>&</b>" not in html


def test_report_to_dict_is_json_shaped():
    history = [record("2026-08-18", 30.00)]
    payload = report_to_dict(
        build_report(history, snapshot(24.00), as_of=date(2026, 8, 19))
    )
    assert payload["asOf"] == "2026-08-19"
    assert payload["baseline"] == "week"
    assert payload["windowDays"] == 7
    assert payload["items"][0]["changePct"] == -20.0
    assert payload["totalChange"] == -6.0
