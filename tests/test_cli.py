import json
from datetime import date

import pytest

from price_tracker import __main__, notify
from price_tracker.__main__ import main


CONFIG = """
list_url: https://www.amazon.com/hz/wishlist/ls/TEST
history_file: {history}
items_file: {items}
notifications:
  enabled: true
  frequency: daily
  baseline: week
  min_change_percent: 1.0
  state_file: {state}
  webhook:
    enabled: true
    url: https://hook.test/x
"""

HISTORY = """date,key,asin,item_id,title,price,currency,available
2026-08-16,B08XYZ1234,B08XYZ1234,I_1,Water Bottle,30.00,USD,true
2026-08-17,B08XYZ1234,B08XYZ1234,I_1,Water Bottle,30.00,USD,true
"""

ITEMS = [
    {
        "key": "B08XYZ1234",
        "asin": "B08XYZ1234",
        "item_id": "I_1",
        "title": "Water Bottle",
        "price": 21.0,
        "currency": "USD",
        "available": True,
        "url": "https://www.amazon.com/dp/B08XYZ1234/",
    }
]


@pytest.fixture
def project(tmp_path):
    history = tmp_path / "price_history.csv"
    items = tmp_path / "items.json"
    state = tmp_path / "notification_state.json"
    history.write_text(HISTORY)
    items.write_text(json.dumps(ITEMS))
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG.format(history=history, items=items, state=state))
    return config, state


def test_report_command_prints_the_digest(project, capsys):
    config, _ = project
    assert main(["--config", str(config), "report"]) == 0
    out = capsys.readouterr().out
    assert "Water Bottle" in out
    assert "$21.00" in out


@pytest.mark.parametrize("fmt", ["html", "json", "webhook"])
def test_report_command_formats(project, capsys, fmt):
    config, _ = project
    assert main(["--config", str(config), "report", "--format", fmt]) == 0
    out = capsys.readouterr().out
    if fmt == "json":
        assert json.loads(out)["items"][0]["currentPrice"] == 21.0
    else:
        assert "Water Bottle" in out


def test_notify_dry_run_sends_nothing(project, capsys, monkeypatch):
    config, state = project

    def fail(*args, **kwargs):
        raise AssertionError("dry run must not deliver")

    monkeypatch.setattr(__main__, "deliver", fail)
    assert main(["--config", str(config), "notify", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "Dry run" in out
    assert "Water Bottle" in out
    assert not state.exists()


def test_notify_delivers_and_records_state(project, capsys, monkeypatch):
    config, state = project
    monkeypatch.setattr(
        notify,
        "send_webhook",
        lambda settings, report: notify.DeliveryResult("webhook", True, "posted (200)"),
    )
    assert main(["--config", str(config), "notify"]) == 0

    out = capsys.readouterr().out
    assert "webhook: ok" in out
    saved = json.loads(state.read_text())
    assert saved["last_sent"] == date.today().isoformat()

    # A second run on the same day is a no-op.
    assert main(["--config", str(config), "notify"]) == 0
    assert "already sent today" in capsys.readouterr().out


def test_notify_exits_nonzero_when_every_channel_fails(project, capsys, monkeypatch):
    config, state = project
    monkeypatch.setattr(
        notify,
        "send_webhook",
        lambda settings, report: notify.DeliveryResult("webhook", False, "boom"),
    )
    assert main(["--config", str(config), "notify"]) == 1
    assert not state.exists()


def test_notify_off_schedule_is_quiet(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(
        "list_url: https://www.amazon.com/hz/wishlist/ls/TEST\n"
        "notifications:\n  enabled: false\n"
    )
    assert main(["--config", str(config), "notify"]) == 0
    assert "disabled" in capsys.readouterr().out


def test_bad_baseline_override_is_a_clean_error(project, capsys):
    config, _ = project
    assert main(["--config", str(config), "report", "--baseline", "fortnight"]) == 1
    assert "Error" in capsys.readouterr().err
