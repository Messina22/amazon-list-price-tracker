import json
import smtplib
from datetime import date

import pytest

from price_tracker import notify
from price_tracker.config import EmailConfig, NotificationsConfig, WebhookConfig
from price_tracker.report import build_report
from tests.test_report import record, snapshot

MONDAY = date(2026, 8, 17)
TUESDAY = date(2026, 8, 18)


def settings(tmp_path, **overrides):
    kwargs = {
        "enabled": True,
        "frequency": "weekly",
        "day_of_week": "monday",
        "baseline": "week",
        "min_change_percent": 1.0,
        "state_file": tmp_path / "notification_state.json",
    }
    kwargs.update(overrides)
    return NotificationsConfig(**kwargs)


def sample_report():
    return build_report(
        [record("2026-08-16", 30.00), record("2026-08-17", 30.00)],
        snapshot(21.00),
        baseline="week",
        as_of=TUESDAY,
    )


def test_weekly_digest_only_fires_on_the_configured_day(tmp_path):
    config = settings(tmp_path)
    due, reason = notify.is_due(config, today=MONDAY)
    assert due is True
    assert "weekly" in reason.lower()

    due, reason = notify.is_due(config, today=TUESDAY)
    assert due is False
    assert "Monday" in reason


def test_daily_digest_fires_every_day(tmp_path):
    config = settings(tmp_path, frequency="daily")
    assert notify.is_due(config, today=MONDAY)[0] is True
    assert notify.is_due(config, today=TUESDAY)[0] is True


def test_disabled_notifications_never_fire(tmp_path):
    config = settings(tmp_path, enabled=False, frequency="daily")
    due, reason = notify.is_due(config, today=MONDAY)
    assert due is False
    assert "disabled" in reason


def test_second_run_on_the_same_day_does_not_resend(tmp_path):
    config = settings(tmp_path, frequency="daily")
    state = {"last_sent": MONDAY.isoformat()}
    due, reason = notify.is_due(config, today=MONDAY, state=state)
    assert due is False
    assert "already sent" in reason
    assert notify.is_due(config, today=TUESDAY, state=state)[0] is True


def test_state_round_trips_through_disk(tmp_path):
    config = settings(tmp_path, frequency="daily")
    report = sample_report()
    results = [notify.DeliveryResult("email", True, "sent")]

    notify.record_sent(config, report, results, today=TUESDAY)
    state = notify.load_state(config.state_file)

    assert state["last_sent"] == "2026-08-18"
    assert state["last_channels"] == ["email"]
    assert state["last_baseline"] == "week"
    assert notify.is_due(config, today=TUESDAY, state=state)[0] is False


def test_load_state_tolerates_a_corrupt_file(tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text("{not json")
    assert notify.load_state(state_file) == {}
    assert notify.load_state(tmp_path / "missing.json") == {}


class FakeSMTP:
    """Stand-in for smtplib.SMTP that records what would have been sent."""

    instances = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host = host
        self.port = port
        self.started_tls = False
        self.login_args = None
        self.messages = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        pass

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message):
        self.messages.append(message)


@pytest.fixture(autouse=True)
def reset_fake_smtp():
    FakeSMTP.instances = []
    yield
    FakeSMTP.instances = []


def email_config(**overrides):
    kwargs = {
        "enabled": True,
        "to": ["you@example.com"],
        "sender": "tracker@example.com",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "security": "starttls",
        "username": "tracker@example.com",
        "password": "hunter2",
    }
    kwargs.update(overrides)
    return EmailConfig(**kwargs)


def test_send_email_builds_a_multipart_message(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    result = notify.send_email(email_config(), sample_report())

    assert result.ok is True
    server = FakeSMTP.instances[0]
    assert server.started_tls is True
    assert server.login_args == ("tracker@example.com", "hunter2")

    message = server.messages[0]
    assert message["To"] == "you@example.com"
    assert "price drop" in message["Subject"]
    body = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    assert "$21.00" in body
    assert "<table" in html


def test_send_email_reports_failures_instead_of_raising(monkeypatch):
    def boom(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"nope")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    result = notify.send_email(email_config(), sample_report())

    assert result.ok is False
    assert "SMTPAuthenticationError" in result.detail


def test_send_webhook_posts_the_rendered_report(monkeypatch):
    import requests

    posted = {}

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

    def fake_post(url, json=None, timeout=None):
        posted["url"] = url
        posted["json"] = json
        return Response()

    monkeypatch.setattr(requests, "post", fake_post)
    result = notify.send_webhook(WebhookConfig(enabled=True, url="https://hook.test/x"), sample_report())

    assert result.ok is True
    assert posted["url"] == "https://hook.test/x"
    assert "Water Bottle" in posted["json"]["text"]
    assert posted["json"]["content"] == posted["json"]["text"]
    assert posted["json"]["report"]["items"][0]["changePct"] == -30.0
    json.dumps(posted["json"])  # must stay serializable


def test_deliver_without_a_channel_is_an_error(tmp_path):
    with pytest.raises(notify.NotifyError):
        notify.deliver(settings(tmp_path), sample_report())


def test_deliver_uses_every_configured_channel(tmp_path, monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(
        notify, "send_webhook", lambda config, report: notify.DeliveryResult("webhook", True, "ok")
    )
    config = settings(
        tmp_path,
        email=email_config(),
        webhook=WebhookConfig(enabled=True, url="https://hook.test/x"),
    )

    results = notify.deliver(config, sample_report())
    assert [result.channel for result in results] == ["email", "webhook"]
    assert all(result.ok for result in results)
